import hashlib
import logging
import re
import time
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse
from uuid import UUID

import feedparser
import httpx
from bs4 import BeautifulSoup
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestError, NotFoundError
from app.models.ai_output import AIOutput
from app.models.enums import BoardType, CreatedBy, Importance, PostStatus, SourceType, TrustLevel
from app.models.post import Post
from app.models.source import Source
from app.schemas.source import CrawlResult
from app.services.crawl_skip_stats import CrawlSkipStats, classify_eval_error
from app.services.ev_relevance import ai_reject_reason, passes_ai_evaluation, passes_keyword_gate
from app.services.llm_client import get_llm_client

logger = logging.getLogger(__name__)

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}


class CrawlerService:
    def __init__(self, db: Session):
        self.db = db
        self.timeout = 15.0
        self.ai_judge_on_crawl = True
        self.fetch_retries = 3
        self.discovery_max_candidates = 15
        # 너무 짧은 텍스트/템플릿성 문구는 저장하지 않기 위한 최소 조건
        self.min_content_len = 220
        self._skip_href = re.compile(
            r"(javascript:|mailto:|#|/css/|\.pdf$|login|logout|signup|search)",
            re.I,
        )

    def _fetch_url(self, url: str) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(self.fetch_retries):
            try:
                with httpx.Client(
                    timeout=self.timeout,
                    follow_redirects=True,
                    headers=_DEFAULT_HEADERS,
                ) as client:
                    resp = client.get(url)
                    resp.raise_for_status()
                    return resp
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as exc:
                last_error = exc
                if attempt + 1 < self.fetch_retries:
                    time.sleep(0.5 * (attempt + 1))
                    continue
                raise
        assert last_error is not None
        raise last_error

    def crawl_source(self, source_id: UUID, organization_id: UUID) -> CrawlResult:
        source = self.db.get(Source, source_id)
        if not source or source.organization_id != organization_id:
            raise NotFoundError("Source not found")
        if not source.is_active:
            raise BadRequestError("Source is inactive")

        if source.source_type == SourceType.rss:
            created, skipped = self._crawl_rss(source)
        else:
            created, skipped = self._crawl_webpage(source)

        source.last_crawled_at = datetime.now(timezone.utc)
        self.db.commit()
        return CrawlResult(
            source_id=source.id,
            created=created,
            skipped=skipped,
            message=f"Created {created}, skipped {skipped}",
        )

    def crawl_source_to_discovery(self, source_id: UUID, organization_id: UUID) -> CrawlResult:
        """
        AI 발견 파이프라인:
        - 소스 목록/피드에서 개별 게시글 후보를 수집
        - AI가 EV·충전 관련성을 판단하고 요약 생성
        - discovery 게시판에는 원문 링크 + AI 요약만 저장 (본문 미저장)
        """
        source = self.db.get(Source, source_id)
        if not source or source.organization_id != organization_id:
            raise NotFoundError("Source not found")
        if not source.is_active:
            raise BadRequestError("Source is inactive")

        stats = CrawlSkipStats()
        if source.source_type == SourceType.rss:
            created, skipped = self._crawl_discovery_rss(source, stats)
        elif source.source_type in (SourceType.news_page, SourceType.notice_page):
            created, skipped = self._crawl_discovery_list_page(source, stats)
        else:
            created, skipped = self._crawl_discovery_single_page(source, stats)

        source.last_crawled_at = datetime.now(timezone.utc)
        self.db.commit()
        return CrawlResult(
            source_id=source.id,
            created=created,
            skipped=skipped,
            message=stats.format_summary(created),
            skip_reasons=stats.to_dict(),
            error_sample=stats.error_sample,
        )

    def _crawl_discovery_rss(self, source: Source, stats: CrawlSkipStats) -> tuple[int, int]:
        feed = feedparser.parse(source.url)
        created = skipped = 0
        for entry in feed.entries[: self.discovery_max_candidates]:
            url = (entry.get("link") or "").strip()
            title = (entry.get("title") or "Untitled").strip()
            if not url:
                skipped += 1
                stats.add("no_url")
                continue

            content = entry.get("summary") or entry.get("description") or ""
            content = self._strip_html(content)
            if len(content) < self.min_content_len:
                try:
                    content = self._fetch_article_text(url, source.source_type)
                except Exception:
                    skipped += 1
                    stats.add("fetch_failed")
                    continue

            published = None
            if entry.get("published_parsed"):
                published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)

            ok, reason = self._process_discovery_candidate(source, title, url, content, published, stats)
            if ok:
                created += 1
            else:
                skipped += 1
                stats.add(reason or "other")
        return created, skipped

    def _crawl_discovery_list_page(self, source: Source, stats: CrawlSkipStats) -> tuple[int, int]:
        resp = self._fetch_url(source.url)
        soup = BeautifulSoup(resp.text, "html.parser")
        candidates = self._extract_article_links(soup, source.url)

        created = skipped = 0
        if not candidates:
            return self._crawl_discovery_single_page(source, stats, soup=soup)

        for title, url in candidates[: self.discovery_max_candidates]:
            try:
                content = self._fetch_article_text(url, source.source_type)
            except Exception:
                skipped += 1
                stats.add("fetch_failed")
                continue
            ok, reason = self._process_discovery_candidate(source, title, url, content, None, stats)
            if ok:
                created += 1
            else:
                skipped += 1
                stats.add(reason or "other")
        return created, skipped

    def _crawl_discovery_single_page(
        self,
        source: Source,
        stats: CrawlSkipStats,
        *,
        soup: BeautifulSoup | None = None,
    ) -> tuple[int, int]:
        if soup is None:
            resp = self._fetch_url(source.url)
            soup = BeautifulSoup(resp.text, "html.parser")
        title = soup.title.string.strip() if soup.title and soup.title.string else source.name
        content = self._extract_main_text(soup, source.source_type)
        content = re.sub(r"\s+", " ", content).strip()[:8000]
        ok, reason = self._process_discovery_candidate(source, title, source.url, content, None, stats)
        if ok:
            return 1, 0
        stats.add(reason or "other")
        return 0, 1

    def _fetch_article_text(self, url: str, source_type: SourceType) -> str:
        resp = self._fetch_url(url)
        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form", "noscript"]):
            tag.decompose()
        text = self._extract_main_text(soup, source_type)
        return re.sub(r"\s+", " ", text).strip()[:8000]

    def _extract_article_links(self, soup: BeautifulSoup, base_url: str) -> list[tuple[str, str]]:
        container = soup.find("article") or soup.find("main") or soup.body
        if not container:
            return []

        base_host = urlparse(base_url).netloc
        seen: set[str] = set()
        results: list[tuple[str, str]] = []

        for anchor in container.find_all("a", href=True):
            href = anchor["href"].strip()
            if not href or self._skip_href.search(href):
                continue
            full_url = urljoin(base_url, href)
            parsed = urlparse(full_url)
            if parsed.scheme not in ("http", "https"):
                continue
            if parsed.netloc and parsed.netloc != base_host:
                continue

            title = anchor.get_text(separator=" ", strip=True)
            if len(title) < 4 or len(title) > 200:
                continue
            if full_url in seen or full_url.rstrip("/") == base_url.rstrip("/"):
                continue

            seen.add(full_url)
            results.append((title, full_url))

        return results

    def _process_discovery_candidate(
        self,
        source: Source,
        title: str,
        url: str,
        content: str,
        published_at: datetime | None,
        stats: CrawlSkipStats,
    ) -> tuple[bool, str | None]:
        title = (title or "").strip()[:512]
        content = (content or "").strip()
        if not title or len(title) < 2:
            return False, "title_invalid"
        if len(content) < self.min_content_len:
            return False, "content_short"

        if not passes_keyword_gate(title, content, url):
            logger.debug("Discovery keyword gate rejected: %s", title[:80])
            return False, "site_junk"

        if stats.billing_depleted:
            stats.add("ai_billing_depleted")
            return False, "ai_billing_depleted"

        try:
            client = get_llm_client()
            evaluation = client.evaluate_discovery_candidate(title, content, url)
        except Exception as exc:
            reason = classify_eval_error(exc)
            logger.warning("Discovery AI evaluation failed for %s: %s", url, exc)
            stats.add(reason, sample=str(exc))
            if reason == "ai_billing_depleted":
                logger.error("Gemini billing depleted — skipping further AI evaluations this run")
            return False, reason

        reject = ai_reject_reason(evaluation, title, content, url)
        if reject:
            logger.debug(
                "Discovery rejected: %s — %s",
                title[:80],
                evaluation.get("relevance_reason") or reject,
            )
            return False, reject

        return self._save_discovery_post(source, title, url, evaluation, published_at)

    def _save_discovery_post(
        self,
        source: Source,
        title: str,
        url: str,
        evaluation: dict,
        published_at: datetime | None,
        *,
        revive_deleted: bool = True,
    ) -> tuple[bool, str | None]:
        imp_raw = evaluation.get("importance", "medium")
        try:
            importance = Importance(imp_raw)
        except ValueError:
            importance = Importance.medium

        content_hash = hashlib.sha256(f"{url}|{title}".encode()).hexdigest()
        existing = self.db.scalar(
            select(Post).where(
                Post.organization_id == source.organization_id,
                Post.content_hash == content_hash,
            )
        )
        if existing:
            if revive_deleted and existing.status == PostStatus.deleted:
                existing.title = title[:512]
                existing.original_url = url
                existing.published_at = published_at
                existing.raw_content = ""
                existing.category = source.category
                existing.board_type = BoardType.discovery
                existing.status = PostStatus.pending
                existing.created_by = CreatedBy.ai_discovery
                existing.importance = importance
                self._attach_discovery_ai_output(existing, evaluation)
                return True, None
            return False, "duplicate"

        post = Post(
            organization_id=source.organization_id,
            source_id=source.id,
            board_type=BoardType.discovery,
            title=title[:512],
            original_url=url,
            published_at=published_at,
            raw_content="",
            content_hash=content_hash,
            category=source.category,
            status=PostStatus.pending,
            trust_level=source.trust_level,
            reliability_score=source.reliability_score,
            importance=importance,
            created_by=CreatedBy.ai_discovery,
        )
        self.db.add(post)
        self.db.flush()
        self._attach_discovery_ai_output(post, evaluation)
        return True, None

    def _attach_discovery_ai_output(self, post: Post, evaluation: dict) -> None:
        imp_raw = evaluation.get("importance", "medium")
        try:
            importance = Importance(imp_raw)
        except ValueError:
            importance = Importance.medium

        client = get_llm_client()
        model_name = getattr(client, "summary_model", "mock")
        output = AIOutput(
            post_id=post.id,
            summary=evaluation.get("summary", ""),
            impact=evaluation.get("impact") or None,
            action_items=evaluation.get("action_items") or None,
            importance=importance,
            confidence=evaluation.get("confidence"),
            model=model_name,
            prompt_version="discovery_v1",
        )
        post.importance = importance
        self.db.add(output)

    def _crawl_rss(
        self,
        source: Source,
        *,
        allow_auto_publish: bool = True,
        created_by: CreatedBy = CreatedBy.crawler,
        revive_deleted: bool = False,
    ) -> tuple[int, int]:
        feed = feedparser.parse(source.url)
        created = skipped = 0
        for entry in feed.entries[:30]:
            url = entry.get("link") or ""
            title = (entry.get("title") or "Untitled").strip()
            if not url:
                skipped += 1
                continue

            content = entry.get("summary") or entry.get("description") or title
            content = self._strip_html(content)
            published = None
            if entry.get("published_parsed"):
                published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            if self._save_post(
                source,
                title,
                url,
                content,
                published,
                allow_auto_publish=allow_auto_publish,
                created_by=created_by,
                revive_deleted=revive_deleted,
            ):
                created += 1
            else:
                skipped += 1
        return created, skipped

    def _crawl_webpage(
        self,
        source: Source,
        *,
        allow_auto_publish: bool = True,
        created_by: CreatedBy = CreatedBy.crawler,
        revive_deleted: bool = False,
    ) -> tuple[int, int]:
        resp = self._fetch_url(source.url)
        soup = BeautifulSoup(resp.text, "html.parser")
        title = soup.title.string.strip() if soup.title and soup.title.string else source.name
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "form", "noscript"]):
            tag.decompose()

        content = self._extract_main_text(soup, source.source_type)
        content = re.sub(r"\s+", " ", content).strip()[:8000]
        created = (
            1
            if self._save_post(
                source,
                title,
                source.url,
                content,
                None,
                allow_auto_publish=allow_auto_publish,
                created_by=created_by,
                revive_deleted=revive_deleted,
            )
            else 0
        )
        skipped = 0 if created else 1
        return created, skipped

    def _strip_html(self, text: str) -> str:
        if "<" not in text:
            return text
        try:
            return BeautifulSoup(text, "html.parser").get_text(separator=" ", strip=True)
        except Exception:
            # worst case: keep original text
            return text

    def _extract_main_text(self, soup: BeautifulSoup, source_type: SourceType) -> str:
        """
        Simple "형식/유형" 대응:
        - news_page/notice_page 는 article/main 안쪽 텍스트를 우선 사용
        - 그 외는 문서 전체 텍스트(이미 nav/footer 등은 제거됨) 사용
        """
        if source_type in (SourceType.news_page, SourceType.notice_page):
            container = soup.find("article") or soup.find("main") or soup.body
        else:
            container = soup

        if not container:
            return ""
        return container.get_text(separator=" ", strip=True)

    def _save_post(
        self,
        source: Source,
        title: str,
        url: str,
        content: str,
        published_at: datetime | None,
        *,
        allow_auto_publish: bool = True,
        created_by: CreatedBy = CreatedBy.crawler,
        revive_deleted: bool = False,
    ) -> bool:
        title = (title or "").strip()[:512]
        content = (content or "").strip()
        if not title or len(title) < 2:
            return False
        if len(content) < self.min_content_len:
            return False

        if not passes_keyword_gate(title, content, url or ""):
            logger.debug("Crawl keyword gate rejected: %s", title[:80])
            return False

        evaluation: dict | None = None
        if self.ai_judge_on_crawl:
            try:
                evaluation = get_llm_client().evaluate_discovery_candidate(title, content, url or "")
            except Exception as exc:
                logger.warning("Crawl AI evaluation failed for %s: %s", url, exc)
                return False
            if not passes_ai_evaluation(evaluation, title, content, url or ""):
                return False

        content_hash = hashlib.sha256(f"{url}|{title}".encode()).hexdigest()
        existing = self.db.scalar(
            select(Post).where(
                Post.organization_id == source.organization_id,
                Post.content_hash == content_hash,
            )
        )
        if existing:
            # Optionally "revive" deleted posts when rerunning pipeline/manual crawl.
            if revive_deleted and existing.status == PostStatus.deleted:
                existing.title = title[:512]
                existing.original_url = url or None
                existing.published_at = published_at
                existing.raw_content = content
                existing.category = source.category
                existing.status = PostStatus.pending
                existing.board_type = BoardType.discovery
                existing.created_by = created_by
                if evaluation:
                    imp_raw = evaluation.get("importance", "medium")
                    try:
                        existing.importance = Importance(imp_raw)
                    except ValueError:
                        existing.importance = Importance.medium
                    self._attach_discovery_ai_output(existing, evaluation)
                    if (
                        allow_auto_publish
                        and source.auto_publish
                        and existing.importance in (Importance.high, Importance.medium)
                    ):
                        existing.board_type = BoardType.trusted
                        existing.status = PostStatus.published
                return True

            return False

        post = Post(
            organization_id=source.organization_id,
            source_id=source.id,
            # 크롤만으로는 바로 trusted로 올리지 않고, 먼저 AI 판단용 discovery에 등록
            board_type=BoardType.discovery,
            title=title[:512],
            original_url=url or None,
            published_at=published_at,
            raw_content=content,
            content_hash=content_hash,
            category=source.category,
            status=PostStatus.pending,
            trust_level=source.trust_level,
            reliability_score=source.reliability_score,
            created_by=created_by,
        )
        self.db.add(post)
        self.db.flush()

        if evaluation:
            imp_raw = evaluation.get("importance", "medium")
            try:
                post.importance = Importance(imp_raw)
            except ValueError:
                post.importance = Importance.medium
            self._attach_discovery_ai_output(post, evaluation)
            if (
                allow_auto_publish
                and source.auto_publish
                and post.importance in (Importance.high, Importance.medium)
            ):
                post.board_type = BoardType.trusted
                post.status = PostStatus.published
            else:
                post.board_type = BoardType.discovery
                post.status = PostStatus.pending

        return True

    def _crawl_source_safe(
        self,
        source: Source,
        organization_id: UUID,
        *,
        to_discovery: bool,
    ) -> CrawlResult:
        try:
            if to_discovery:
                return self.crawl_source_to_discovery(source.id, organization_id)
            return self.crawl_source(source.id, organization_id)
        except Exception as exc:
            logger.warning("Crawl failed for source %s (%s): %s", source.id, source.url, exc)
            self.db.rollback()
            return CrawlResult(
                source_id=source.id,
                created=0,
                skipped=0,
                message="Crawl failed",
                error=str(exc),
            )

    def crawl_all_active(self, organization_id: UUID) -> list[CrawlResult]:
        sources = self.db.scalars(
            select(Source).where(Source.organization_id == organization_id, Source.is_active.is_(True))
        ).all()
        results = []
        for source in sources:
            results.append(self._crawl_source_safe(source, organization_id, to_discovery=False))
        return results

    def crawl_all_active_to_discovery(
        self, organization_id: UUID, *, trusted_only: bool = True
    ) -> list[CrawlResult]:
        q = select(Source).where(Source.organization_id == organization_id, Source.is_active.is_(True))
        if trusted_only:
            q = q.where(Source.trust_level == TrustLevel.high)
        sources = self.db.scalars(q).all()
        results: list[CrawlResult] = []
        for source in sources:
            results.append(self._crawl_source_safe(source, organization_id, to_discovery=True))
        return results
