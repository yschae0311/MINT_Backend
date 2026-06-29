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

from app.core.config import get_settings
from app.models.ai_output import AIOutput
from app.models.enums import BoardType, CreatedBy, Importance, PostStatus, SourceType, TrustLevel
from app.models.post import Post
from app.models.source import Source
from app.schemas.source import CrawlResult
from app.services.community_sources import (
    COMMUNITY_MIN_CONTENT_LEN,
    COMMUNITY_SOURCE_TYPES,
    REDDIT_REQUEST_DELAY_SEC,
    REDDIT_USER_AGENT,
    extract_forum_article_links,
    extract_community_article_text,
    is_community_source_type,
    reddit_old_post_url,
)
from app.services.crawl_skip_stats import CrawlSkipStats, classify_eval_error
from app.services.gov_sources import (
    extract_gov_article_links,
    extract_gov_article_text,
    is_gov_notice_host,
)
from app.services.ev_relevance import ai_reject_reason, passes_ai_evaluation, passes_keyword_gate
from app.services.llm_client import get_llm_client
from app.services.post_dedup import compute_content_hash, find_existing_post
from app.search.post_content import BODY_MAX_CHARS, clear_pg_text_fields, pg_ai_summary_placeholder, save_post_content
from app.services.reddit_client import (
    RedditClient,
    is_reddit_access_denied,
    reddit_fetch_hint,
    reddit_post_from_raw,
)
from app.services.title_translation import localized_title_for_storage

logger = logging.getLogger(__name__)

_DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}


def _resolve_feed_link(feed_url: str, link: str) -> str:
    """RSS item link을 절대 URL로 변환 (mcee.go.kr 등 상대 경로 대응)."""
    link = (link or "").strip()
    if not link:
        return ""
    if link.startswith(("http://", "https://")):
        return link
    return urljoin(feed_url, link)


class CrawlerService:
    def __init__(self, db: Session):
        self.db = db
        self.timeout = 15.0
        self.ai_judge_on_crawl = True
        self.fetch_retries = 3
        self.discovery_max_candidates = 15
        self.community_max_candidates = 10
        # 너무 짧은 텍스트/템플릿성 문구는 저장하지 않기 위한 최소 조건
        self.min_content_len = 220
        self._skip_href = re.compile(
            r"(javascript:|mailto:|#|/css/|\.pdf$|/login(?:/|$)|logout|signup|/search(?:/|$|\?))",
            re.I,
        )
        self.trusted_list_max_candidates = 15

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

    def _is_community_source(self, source: Source) -> bool:
        return is_community_source_type(source.source_type)

    def _min_content_len_for(self, source: Source) -> int:
        if self._is_community_source(source):
            return COMMUNITY_MIN_CONTENT_LEN
        return self.min_content_len

    def _fetch_reddit(self, url: str, *, accept: str) -> httpx.Response:
        headers = {
            **_DEFAULT_HEADERS,
            "User-Agent": REDDIT_USER_AGENT,
            "Accept": accept,
        }
        proxy = get_settings().reddit_http_proxy.strip() or None
        last_error: Exception | None = None
        for attempt in range(self.fetch_retries):
            try:
                with httpx.Client(
                    timeout=self.timeout,
                    follow_redirects=True,
                    headers=headers,
                    proxy=proxy,
                ) as client:
                    resp = client.get(url)
                    if resp.status_code == 429:
                        time.sleep(REDDIT_REQUEST_DELAY_SEC * (attempt + 2))
                        continue
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

        # 커뮤니티 소스는 중요 게시판 크롤 대신 탐문 파이프라인만 사용
        if self._is_community_source(source):
            return self.crawl_source_to_discovery(source_id, organization_id)

        if source.source_type == SourceType.rss:
            created, skipped = self._crawl_rss(source)
        elif source.source_type in (SourceType.news_page, SourceType.notice_page):
            created, skipped = self._crawl_trusted_list_page(source)
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
        if source.source_type == SourceType.reddit:
            created, skipped = self._crawl_discovery_reddit(source, stats)
        elif source.source_type == SourceType.community_forum:
            created, skipped = self._crawl_discovery_community_forum(source, stats)
        elif source.source_type == SourceType.rss:
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

    def _crawl_discovery_reddit(self, source: Source, stats: CrawlSkipStats) -> tuple[int, int]:
        candidates = self._fetch_reddit_listing_candidates(source, stats)
        if not candidates:
            return 0, 0

        created = skipped = 0
        for i, (title, post_url, content, published) in enumerate(
            candidates[: self.community_max_candidates]
        ):
            if i > 0:
                time.sleep(REDDIT_REQUEST_DELAY_SEC)

            if not title or not post_url:
                skipped += 1
                stats.add("no_url")
                continue

            content = self._enrich_reddit_post_content(title, post_url, content)

            ok, reason = self._process_discovery_candidate(
                source, title, post_url, content, published, stats
            )
            if ok:
                created += 1
            else:
                skipped += 1
                stats.add(reason or "other")
        return created, skipped

    def _fetch_reddit_listing_candidates(
        self, source: Source, stats: CrawlSkipStats
    ) -> list[tuple[str, str, str, datetime | None]] | None:
        client = RedditClient()
        try:
            raw_posts = client.fetch_listing(source.url, limit=self.community_max_candidates)
        except Exception as exc:
            logger.warning("Reddit listing fetch failed for %s: %s", source.url, exc)
            reason = "reddit_blocked" if is_reddit_access_denied(exc) else "fetch_failed"
            stats.add(reason, sample=reddit_fetch_hint(exc, client))
            return None

        if not raw_posts:
            stats.add("reddit_blocked", sample=reddit_fetch_hint(None, client))
            return None

        return [reddit_post_from_raw(post) for post in raw_posts]

    def _enrich_reddit_post_content(self, title: str, post_url: str, content: str) -> str:
        content = (content or "").strip()
        if len(content) < COMMUNITY_MIN_CONTENT_LEN:
            content = f"{title}\n\n{content}".strip()
        if len(content) >= COMMUNITY_MIN_CONTENT_LEN:
            return content
        extra = self._fetch_reddit_post_selftext(post_url)
        if extra:
            content = f"{title}\n\n{extra}".strip()
        if len(content) < COMMUNITY_MIN_CONTENT_LEN:
            content = f"{title}\n\n(Reddit 게시글 — 본문이 짧거나 링크 공유입니다.)"
        return content

    def _fetch_reddit_post_selftext(self, post_url: str) -> str:
        if "reddit.com" not in post_url:
            return ""
        old_url = reddit_old_post_url(post_url)
        try:
            resp = self._fetch_reddit(old_url, accept="text/html,application/xhtml+xml,*/*;q=0.8")
            soup = BeautifulSoup(resp.text, "html.parser")
            body = soup.select_one("div.expando div.usertext-body") or soup.select_one(
                "form.usertext div.usertext-body"
            )
            if body:
                return body.get_text(" ", strip=True)
        except Exception as exc:
            logger.debug("Reddit post fetch failed for %s: %s", post_url, exc)
        return ""

    def _crawl_discovery_community_forum(self, source: Source, stats: CrawlSkipStats) -> tuple[int, int]:
        resp = self._fetch_url(source.url)
        soup = BeautifulSoup(resp.text, "html.parser")
        candidates = extract_forum_article_links(soup, source.url, skip_href=self._skip_href)
        if not candidates:
            candidates = self._extract_article_links(soup, source.url)

        created = skipped = 0
        if not candidates:
            return self._crawl_discovery_single_page(source, stats, soup=soup)

        for title, url in candidates[: self.community_max_candidates]:
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

    def _crawl_discovery_rss(self, source: Source, stats: CrawlSkipStats) -> tuple[int, int]:
        feed = feedparser.parse(source.url)
        created = skipped = 0
        for entry in feed.entries[: self.discovery_max_candidates]:
            url = _resolve_feed_link(source.url, entry.get("link") or "")
            title = (entry.get("title") or "Untitled").strip()
            if not url:
                skipped += 1
                stats.add("no_url")
                continue

            content = entry.get("summary") or entry.get("description") or ""
            content = self._strip_html(content)
            min_len = self._min_content_len_for(source)
            if len(content) < min_len:
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
        content = re.sub(r"\s+", " ", content).strip()[:BODY_MAX_CHARS]
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
        text = extract_community_article_text(soup, url)
        if not text:
            text = extract_gov_article_text(soup, url)
        if not text:
            text = self._extract_main_text(soup, source_type)
        return re.sub(r"\s+", " ", text).strip()[:BODY_MAX_CHARS]

    def _extract_article_links(self, soup: BeautifulSoup, base_url: str) -> list[tuple[str, str]]:
        if is_gov_notice_host(base_url):
            gov_links = extract_gov_article_links(soup, base_url, skip_href=self._skip_href)
            if gov_links:
                return gov_links

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
        min_len = self._min_content_len_for(source)
        if not title or len(title) < 2:
            return False, "title_invalid"
        if len(content) < min_len:
            return False, "content_short"

        if not passes_keyword_gate(title, content, url):
            from app.services.ev_relevance import is_weak_topic_only

            reason = "weak_topic_only" if is_weak_topic_only(title, content, url) else "ai_topic_mismatch"
            logger.debug("Discovery keyword gate rejected (%s): %s", reason, title[:80])
            return False, reason

        if stats.billing_depleted:
            stats.add("ai_billing_depleted")
            return False, "ai_billing_depleted"

        try:
            client = get_llm_client()
            evaluation = client.evaluate_discovery_candidate(
                title, content, url, community=self._is_community_source(source)
            )
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

        return self._save_discovery_post(
            source, title, url, evaluation, published_at, body=content
        )

    def _save_discovery_post(
        self,
        source: Source,
        title: str,
        url: str,
        evaluation: dict,
        published_at: datetime | None,
        *,
        body: str = "",
        revive_deleted: bool = True,
    ) -> tuple[bool, str | None]:
        imp_raw = evaluation.get("importance", "medium")
        try:
            importance = Importance(imp_raw)
        except ValueError:
            importance = Importance.medium

        content_hash = compute_content_hash(url, title)
        stored_title = localized_title_for_storage(title)
        existing = find_existing_post(
            self.db,
            source.organization_id,
            url,
            title,
            include_deleted=revive_deleted,
        )
        stored_body = (body or "").strip()
        if existing:
            if revive_deleted and existing.status == PostStatus.deleted:
                existing.title = stored_title[:512]
                existing.published_at = published_at
                existing.category = source.category
                existing.board_type = BoardType.discovery
                existing.status = PostStatus.pending
                existing.created_by = CreatedBy.ai_discovery
                existing.importance = importance
                clear_pg_text_fields(existing)
                self._attach_discovery_ai_output(
                    existing,
                    evaluation,
                    community=self._is_community_source(source),
                    original_url=url or None,
                    body=stored_body,
                )
                return True, None
            return False, "duplicate"

        post = Post(
            organization_id=source.organization_id,
            source_id=source.id,
            board_type=BoardType.discovery,
            title=stored_title[:512],
            original_url=None if get_settings().search_uses_elasticsearch else (url or None),
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
        self._attach_discovery_ai_output(
            post,
            evaluation,
            community=self._is_community_source(source),
            original_url=url or None,
            body=stored_body,
        )
        return True, None

    def _attach_discovery_ai_output(
        self,
        post: Post,
        evaluation: dict,
        *,
        community: bool = False,
        original_url: str | None = None,
        body: str = "",
    ) -> None:
        imp_raw = evaluation.get("importance", "medium")
        try:
            importance = Importance(imp_raw)
        except ValueError:
            importance = Importance.medium

        client = get_llm_client()
        model_name = getattr(client, "summary_model", "mock")
        prompt_version = "community_v1" if community else "discovery_v2"
        use_es = get_settings().search_uses_elasticsearch
        output = AIOutput(
            post_id=post.id,
            summary=pg_ai_summary_placeholder() if use_es else evaluation.get("summary", ""),
            impact=None if use_es else (evaluation.get("impact") or None),
            action_items=None if use_es else (evaluation.get("action_items") or None),
            importance=importance,
            confidence=evaluation.get("confidence"),
            model=model_name,
            prompt_version=prompt_version,
        )
        post.importance = importance
        self.db.add(output)
        self.db.flush()
        if use_es:
            save_post_content(
                self.db,
                post,
                original_url=original_url,
                summary=evaluation.get("summary", ""),
                impact=evaluation.get("impact") or None,
                body=body,
                action_items=evaluation.get("action_items") or None,
                merge_existing=True,
            )
        elif original_url:
            post.original_url = original_url
        from app.services.personalization_service import ClassificationService

        ClassificationService(self.db).classify_post(post, evaluation)

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
            url = _resolve_feed_link(source.url, entry.get("link") or "")
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

    def _crawl_trusted_list_page(self, source: Source) -> tuple[int, int]:
        resp = self._fetch_url(source.url)
        soup = BeautifulSoup(resp.text, "html.parser")
        candidates = self._extract_article_links(soup, source.url)

        if not candidates:
            return self._crawl_webpage(source)

        created = skipped = 0
        for title, url in candidates[: self.trusted_list_max_candidates]:
            try:
                content = self._fetch_article_text(url, source.source_type)
            except Exception:
                skipped += 1
                continue
            if self._save_post(source, title, url, content, None):
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
        content = re.sub(r"\s+", " ", content).strip()[:BODY_MAX_CHARS]
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
        if source_type in (SourceType.news_page, SourceType.notice_page, SourceType.community_forum):
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
        if len(content) < self._min_content_len_for(source):
            return False

        if not passes_keyword_gate(title, content, url or ""):
            logger.debug("Crawl keyword gate rejected: %s", title[:80])
            return False  # skipped without detailed reason in regular crawl stats

        evaluation: dict | None = None
        if self.ai_judge_on_crawl:
            try:
                evaluation = get_llm_client().evaluate_discovery_candidate(
                    title, content, url or "", community=self._is_community_source(source)
                )
            except Exception as exc:
                logger.warning("Crawl AI evaluation failed for %s: %s", url, exc)
                return False
            if not passes_ai_evaluation(evaluation, title, content, url or ""):
                return False

        content_hash = compute_content_hash(url, title)
        stored_title = localized_title_for_storage(title)
        existing = find_existing_post(
            self.db,
            source.organization_id,
            url,
            title,
            include_deleted=revive_deleted,
        )
        if existing:
            # Optionally "revive" deleted posts when rerunning pipeline/manual crawl.
            if revive_deleted and existing.status == PostStatus.deleted:
                existing.title = stored_title[:512]
                existing.published_at = published_at
                existing.category = source.category
                existing.status = PostStatus.pending
                existing.board_type = BoardType.discovery
                existing.created_by = created_by
                clear_pg_text_fields(existing)
                if evaluation:
                    imp_raw = evaluation.get("importance", "medium")
                    try:
                        existing.importance = Importance(imp_raw)
                    except ValueError:
                        existing.importance = Importance.medium
                    self._attach_discovery_ai_output(
                        existing,
                        evaluation,
                        community=self._is_community_source(source),
                        original_url=url or None,
                        body=content,
                    )
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
            title=stored_title[:512],
            original_url=None if get_settings().search_uses_elasticsearch else (url or None),
            published_at=published_at,
            raw_content="" if get_settings().search_uses_elasticsearch else content,
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
            self._attach_discovery_ai_output(
                post,
                evaluation,
                community=self._is_community_source(source),
                original_url=url or None,
                body=content,
            )
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
        else:
            if get_settings().search_uses_elasticsearch:
                save_post_content(
                    self.db,
                    post,
                    original_url=url or None,
                    body=content,
                    merge_existing=False,
                )
            else:
                post.original_url = url or None
                post.raw_content = content

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
            select(Source).where(
                Source.organization_id == organization_id,
                Source.is_active.is_(True),
                Source.source_type.not_in(tuple(COMMUNITY_SOURCE_TYPES)),
                Source.trust_level != TrustLevel.low,
            )
        ).all()
        results = []
        for source in sources:
            results.append(self._crawl_source_safe(source, organization_id, to_discovery=False))
        return results

    def crawl_all_active_to_discovery(
        self, organization_id: UUID, *, trusted_only: bool = True, community_only: bool = False
    ) -> list[CrawlResult]:
        q = select(Source).where(Source.organization_id == organization_id, Source.is_active.is_(True))
        if community_only:
            q = q.where(Source.source_type.in_(tuple(COMMUNITY_SOURCE_TYPES)))
        elif trusted_only:
            q = q.where(Source.trust_level == TrustLevel.high)
        sources = self.db.scalars(q).all()
        results: list[CrawlResult] = []
        for source in sources:
            results.append(self._crawl_source_safe(source, organization_id, to_discovery=True))
        return results

    def submit_community_url(
        self,
        organization_id: UUID,
        url: str,
        *,
        title: str | None = None,
        note: str = "",
    ) -> tuple[bool, str | None]:
        """Fetch a single community URL and enqueue it for discovery review."""
        stats = CrawlSkipStats()
        manual_source = self.db.scalar(
            select(Source).where(
                Source.organization_id == organization_id,
                Source.source_type == SourceType.community_forum,
                Source.name == "__community_submit__",
            )
        )
        if not manual_source:
            manual_source = Source(
                organization_id=organization_id,
                name="__community_submit__",
                url="manual://community-submit",
                source_type=SourceType.community_forum,
                category="커뮤니티/현장",
                trust_level=TrustLevel.low,
                reliability_score=45,
                auto_publish=False,
                is_active=False,
            )
            self.db.add(manual_source)
            self.db.flush()

        page_title = (title or "").strip() or url
        try:
            content = self._fetch_article_text(url, SourceType.community_forum)
        except Exception as exc:
            logger.warning("Community URL fetch failed for %s: %s", url, exc)
            return False, "fetch_failed"

        if note:
            content = f"{note.strip()}\n\n{content}".strip()

        ok, reason = self._process_discovery_candidate(
            manual_source, page_title, url, content, None, stats
        )
        if ok:
            post = find_existing_post(self.db, organization_id, url, page_title)
            if post:
                post.created_by = CreatedBy.user_submitted
            self.db.commit()
            return True, None

        self.db.rollback()
        return False, reason or "rejected"
