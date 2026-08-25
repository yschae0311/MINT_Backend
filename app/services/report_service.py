import logging
from pathlib import Path
from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.config import get_settings
from app.core.exceptions import BadRequestError, NotFoundError
from app.models.daily_report import DailyReport, DailyReportItem
from app.models.enums import BoardType, Importance, PostStatus
from app.models.post import Post
from app.schemas.post import PostRead
from app.schemas.report import DailyReportDetail, DailyReportItemRead, DailyReportRead
from app.search.post_content import get_post_content, legacy_pg_content_enabled, pg_ai_summary_placeholder
from app.services.llm_client import get_llm_client
from app.services.report_illustration_service import ReportIllustrationService

KST = ZoneInfo("Asia/Seoul")
logger = logging.getLogger(__name__)


def format_report_title(report_date: date, edition_name: str | None = None) -> str:
    if edition_name:
        return f"MINT 브리핑 · {edition_name} · {report_date.isoformat()}"
    return f"MINT 브리핑 · {report_date.isoformat()}"


class ReportService:
    def __init__(self, db: Session):
        self.db = db

    def list_reports(
        self,
        organization_id: UUID,
        *,
        edition_id: UUID | None = None,
        edition_ids: set[UUID] | None = None,
    ) -> list[DailyReportRead]:
        q = select(DailyReport).where(DailyReport.organization_id == organization_id)
        if edition_id:
            q = q.where(DailyReport.edition_id == edition_id)
        elif edition_ids is not None:
            if not edition_ids:
                return []
            q = q.where(DailyReport.edition_id.in_(edition_ids))
        reports = self.db.scalars(q.order_by(DailyReport.report_date.desc())).all()
        return [DailyReportRead.model_validate(r) for r in reports]

    def latest_for_edition(self, organization_id: UUID, edition_id: UUID) -> DailyReport | None:
        row = self.db.scalar(
            select(DailyReport)
            .where(
                DailyReport.organization_id == organization_id,
                DailyReport.edition_id == edition_id,
            )
            .order_by(DailyReport.report_date.desc(), DailyReport.created_at.desc())
            .limit(1)
        )
        if row:
            return row
        from app.services.edition_service import EV_SLUG, EditionService

        edition = EditionService(self.db).get(edition_id, organization_id)
        if edition.slug != EV_SLUG:
            return None
        return self.db.scalar(
            select(DailyReport)
            .where(
                DailyReport.organization_id == organization_id,
                DailyReport.edition_id.is_(None),
            )
            .order_by(DailyReport.report_date.desc(), DailyReport.created_at.desc())
            .limit(1)
        )

    def delete_report(self, report_id: UUID, organization_id: UUID) -> None:
        report = self.db.scalar(
            select(DailyReport).where(
                DailyReport.id == report_id,
                DailyReport.organization_id == organization_id,
            )
        )
        if not report:
            raise NotFoundError("Report not found")
        self.db.delete(report)
        self.db.commit()

    def get_report(self, report_id: UUID, organization_id: UUID) -> DailyReportDetail:
        report = self.db.scalar(
            select(DailyReport)
            .options(joinedload(DailyReport.items))
            .where(DailyReport.id == report_id, DailyReport.organization_id == organization_id)
        )
        if not report:
            raise NotFoundError("Report not found")

        detail = DailyReportDetail.model_validate(report)
        items = []
        for item in report.items:
            post = self.db.get(Post, item.post_id)
            row = DailyReportItemRead.model_validate(item)
            if post:
                row.post = PostRead.model_validate(post)
                row.post.source_name = post.source.name if post.source else None
            items.append(row)
        detail.items = items
        return detail

    def _resolve_report_date(self, report_date: date | None, *, prefer_yesterday: bool) -> date:
        if report_date is not None:
            return report_date
        today_kst = datetime.now(KST).date()
        if prefer_yesterday:
            return today_kst - timedelta(days=1)
        return today_kst

    def _day_range(self, target: date) -> tuple[datetime, datetime]:
        start = datetime.combine(target, datetime.min.time(), tzinfo=KST)
        return start, start + timedelta(days=1)

    def _post_summary_for_report(self, post: Post) -> str:
        content = get_post_content(self.db, post.id)
        if content.body and content.body.strip():
            return content.body.strip()[:500]
        if content.summary and content.summary.strip():
            return content.summary.strip()[:500]
        if legacy_pg_content_enabled() and post.ai_outputs:
            latest = max(post.ai_outputs, key=lambda o: o.created_at)
            summary = (latest.summary or "").strip()
            if summary and summary != pg_ai_summary_placeholder().strip():
                return summary[:500]
        return post.title

    def _post_url(self, post: Post) -> str:
        original = getattr(post, "original_url", None) or ""
        if original:
            return original
        source = getattr(post, "source", None)
        if source is not None and getattr(source, "url", None):
            return source.url or ""
        return ""

    def _edition_source_ids(self, organization_id: UUID, edition_id: UUID) -> set[UUID]:
        from app.models.edition import SourceEdition
        from app.models.source import Source

        return set(
            self.db.scalars(
                select(SourceEdition.source_id)
                .join(Source, Source.id == SourceEdition.source_id)
                .where(
                    Source.organization_id == organization_id,
                    SourceEdition.edition_id == edition_id,
                )
            ).all()
        )

    def _post_matches_edition(
        self,
        post: Post,
        *,
        body: str,
        edition,
        topic_terms: list[str],
        source_ids: set[UUID],
    ) -> bool:
        from app.services.edition_service import AUTONOMOUS_SLUG, EV_SLUG
        from app.services.ev_relevance import (
            has_strong_av_signal,
            has_strong_ev_signal,
            is_obvious_junk,
            matches_topic_terms,
        )

        title = (post.title or "").strip()
        url = self._post_url(post)
        if is_obvious_junk(title, body, url):
            return False
        if post.source_id and post.source_id in source_ids:
            return True
        if edition.slug == EV_SLUG:
            return has_strong_ev_signal(title, body, url) or matches_topic_terms(
                title, body, url, topic_terms
            )
        if edition.slug == AUTONOMOUS_SLUG:
            return has_strong_av_signal(title, body, url) or matches_topic_terms(
                title, body, url, topic_terms
            )
        return matches_topic_terms(title, body, url, topic_terms)

    def generate(
        self,
        organization_id: UUID,
        report_date: date | None = None,
        *,
        prefer_yesterday: bool = False,
        allow_empty: bool = False,
        edition_id: UUID | None = None,
    ) -> DailyReportDetail:
        target = self._resolve_report_date(report_date, prefer_yesterday=prefer_yesterday)
        start, end = self._day_range(target)

        posts = self.db.scalars(
            select(Post)
            .options(joinedload(Post.source), joinedload(Post.ai_outputs))
            .where(
                Post.organization_id == organization_id,
                Post.collected_at >= start,
                Post.collected_at < end,
                Post.status.in_([PostStatus.published, PostStatus.pending]),
            )
        ).unique().all()

        from app.search.post_content import mget_post_contents
        from app.services.edition_service import EditionService
        from app.models.personalization import PostKeyword

        edition_svc = EditionService(self.db)
        if edition_id is None:
            ev = edition_svc.ev_edition(organization_id)
            edition_id = ev.id if ev else None

        edition = None
        topic_terms: list[str] = []
        featured_ids: list[UUID] = []
        source_ids: set[UUID] = set()
        if edition_id:
            edition = edition_svc.get(edition_id, organization_id)
            from app.services.topic_gate import load_edition_topic_terms

            topic_terms = load_edition_topic_terms(self.db, organization_id, edition_id)
            featured_ids = edition_svc.featured_keyword_ids(organization_id, edition_id)
            source_ids = self._edition_source_ids(organization_id, edition_id)

        contents = mget_post_contents(self.db, [p.id for p in posts])
        scoped_posts = []
        for p in posts:
            content = contents.get(p.id)
            body = ""
            if content is not None:
                body = (content.body or content.summary or "")[:4000]
            if edition is None:
                from app.services.ev_display_filter import is_ev_related_post
                from app.services.topic_gate import load_topic_terms

                extra_terms = load_topic_terms(self.db, organization_id)
                if is_ev_related_post(p, body=body, extra_terms=extra_terms):
                    scoped_posts.append(p)
            elif self._post_matches_edition(
                p,
                body=body,
                edition=edition,
                topic_terms=topic_terms,
                source_ids=source_ids,
            ):
                scoped_posts.append(p)

        if featured_ids:
            post_ids = [p.id for p in scoped_posts]
            matched_ids: set[UUID] = set()
            if post_ids:
                matched_ids = set(
                    self.db.scalars(
                        select(PostKeyword.post_id).where(
                            PostKeyword.post_id.in_(post_ids),
                            PostKeyword.keyword_id.in_(featured_ids),
                        )
                    ).all()
                )
            featured_posts = [p for p in scoped_posts if p.id in matched_ids]
            if featured_posts:
                scoped_posts = featured_posts

        eligible = [
            p
            for p in scoped_posts
            if p.board_type in (BoardType.trusted, BoardType.discovery)
        ]
        trusted_posts = [p for p in eligible if p.board_type == BoardType.trusted]
        discovery_posts = [p for p in eligible if p.board_type == BoardType.discovery]

        if not eligible:
            if not allow_empty:
                raise BadRequestError(
                    f"{target.isoformat()} 기준으로 리포트에 포함할 게시글이 없습니다. "
                    "해당 날짜에 수집된 중요/AI 발견 게시글이 있는지 확인하세요."
                )
            return self._create_empty_report(organization_id, target, edition_id=edition_id)

        # 중요 게시판 우선, AI 발견 게시판을 함께 포함 (최대 40건)
        ordered = trusted_posts + discovery_posts
        payload = [
            {
                "id": str(p.id),
                "title": p.title,
                "summary": self._post_summary_for_report(p),
                "board": "trusted" if p.board_type == BoardType.trusted else "discovery",
                "importance": p.importance.value if p.importance != Importance.unknown else "medium",
            }
            for p in ordered[:40]
        ]
        client = get_llm_client()
        edition_payload = None
        if edition is not None:
            edition_payload = {
                "name": edition.name,
                "slug": edition.slug,
                "topics": topic_terms[:40],
            }
        result = client.generate_daily_report(payload, target, edition=edition_payload)
        normalized = self._normalize_report_result(result, target)
        edition_name = edition.name if edition is not None else None
        normalized["title"] = format_report_title(target, edition_name)

        report = DailyReport(
            organization_id=organization_id,
            edition_id=edition_id,
            report_date=target,
            title=normalized["title"],
            summary=normalized["summary"],
            key_changes=normalized["key_changes"],
            risks=normalized.get("risks"),
            action_items=normalized.get("action_items"),
            model=getattr(client, "report_model", "mock"),
            prompt_version="v3",
        )
        self.db.add(report)
        self.db.flush()

        posts_by_id = {str(p.id): p for p in ordered}
        related_ids: list[str] = []
        rec_by_post: dict[str, str] = {}
        for change in normalized["key_changes"]:
            why = (change.get("description") or "").strip()
            for pid in change.get("related_post_ids") or []:
                pid_str = str(pid)
                if pid_str not in related_ids:
                    related_ids.append(pid_str)
                if why and pid_str not in rec_by_post:
                    rec_by_post[pid_str] = why

        item_posts: list[Post] = []
        for pid in related_ids:
            post = posts_by_id.get(pid)
            if post and post not in item_posts:
                item_posts.append(post)
        for post in ordered:
            if post.importance == Importance.high and post not in item_posts:
                item_posts.append(post)
        if not item_posts:
            item_posts = ordered[:10]

        for post in item_posts[:20]:
            board_label = "중요" if post.board_type == BoardType.trusted else "AI발견"
            why = rec_by_post.get(str(post.id))
            reason = f"[{board_label}] {why}" if why else f"[{board_label}] {post.title[:120]}"
            self.db.add(
                DailyReportItem(
                    report_id=report.id,
                    post_id=post.id,
                    reason=reason[:200],
                    importance=post.importance if post.importance != Importance.unknown else Importance.medium,
                )
            )

        self._attach_illustration(report, client, normalized)

        self.db.commit()
        return self.get_report(report.id, organization_id)

    def _attach_illustration(self, report: DailyReport, client, normalized: dict) -> None:
        settings = get_settings()
        from app.services.bedrock_runtime import illustration_provider_ready

        if not illustration_provider_ready(settings):
            return
        try:
            scene = client.generate_report_illustration_scene(
                normalized["summary"],
                normalized["key_changes"],
                report.report_date,
            )
            illus = ReportIllustrationService()
            image_bytes = illus.generate_image_bytes(scene)
            if image_bytes:
                report.illustration_url = illus.save_for_report(report.id, image_bytes)
        except Exception as exc:
            logger.warning("Report illustration skipped for %s: %s", report.id, exc)

    def ensure_illustration(self, report_id: UUID, organization_id: UUID) -> str:
        """Generate and persist a newspaper sketch if the report has none yet."""
        report = self.db.scalar(
            select(DailyReport).where(
                DailyReport.id == report_id,
                DailyReport.organization_id == organization_id,
            )
        )
        if not report:
            raise NotFoundError("Report not found")
        if report.illustration_url:
            return report.illustration_url

        settings = get_settings()
        from app.services.bedrock_runtime import illustration_provider_ready

        if not illustration_provider_ready(settings):
            raise BadRequestError("일러스트 생성이 비활성화되어 있거나 이미지 모델이 없습니다.")

        highlights = report.key_changes if isinstance(report.key_changes, list) else []
        client = get_llm_client()
        try:
            scene = client.generate_report_illustration_scene(
                report.summary or report.title,
                highlights if isinstance(highlights, list) else [],
                report.report_date,
            )
        except Exception:
            scene = (
                f"Electric vehicle industry metaphor for daily briefing titled "
                f"'{report.title[:120]}'"
            )

        illus = ReportIllustrationService()
        image_bytes = illus.generate_image_bytes(scene)
        if not image_bytes:
            raise BadRequestError("이미지 생성에 실패했습니다.")

        report.illustration_url = illus.save_for_report(report.id, image_bytes)
        self.db.commit()
        return report.illustration_url

    def ensure_front_photo(
        self,
        organization_id: UUID,
        *,
        report_id: UUID | None = None,
        title: str | None = None,
        summary: str | None = None,
        seed: str | None = None,
        force: bool = False,
    ) -> str:
        """Return today's front-page illustration (generate at most once per KST day)."""
        _ = seed  # retained for API compat; daily cache is date-keyed
        settings = get_settings()
        from app.services.bedrock_runtime import illustration_provider_ready

        if not illustration_provider_ready(settings):
            raise BadRequestError("일러스트 생성이 비활성화되어 있거나 이미지 모델이 없습니다.")

        today = datetime.now(KST).date().isoformat()
        illus = ReportIllustrationService()

        if force:
            self._clear_today_front_photo(organization_id, today, report_id, illus)

        cached = illus.read_front_cache(organization_id, today)
        if cached:
            self._link_report_illustration(report_id, organization_id, cached)
            return cached

        report = None
        if report_id is not None:
            report = self.db.scalar(
                select(DailyReport).where(
                    DailyReport.id == report_id,
                    DailyReport.organization_id == organization_id,
                )
            )
            if report and report.illustration_url and not force:
                # Reuse existing report art for today without regenerating.
                try:
                    src = Path(settings.media_root) / report.illustration_url.lstrip("/").removeprefix(
                        settings.media_url_prefix.lstrip("/") + "/"
                    )
                    if not src.is_file():
                        # illustration_url is like /media/reports/uuid.png
                        rel = report.illustration_url
                        if rel.startswith(settings.media_url_prefix):
                            rel = rel[len(settings.media_url_prefix) :].lstrip("/")
                        src = Path(settings.media_root) / rel
                    if src.is_file():
                        return illus.save_front_cache(organization_id, today, src.read_bytes())
                except OSError:
                    pass
                return report.illustration_url

        headline = (title or (report.title if report else "") or "").strip() or "EV industry daily briefing"
        blurb = (summary or (report.summary if report else "") or "").strip()
        scene = (
            f"Metaphorical editorial scene for EV / charging news headline: {headline[:160]}. "
            f"{blurb[:180]}"
        ).strip()

        if report:
            highlights = report.key_changes if isinstance(report.key_changes, list) else []
            client = get_llm_client()
            try:
                scene = client.generate_report_illustration_scene(
                    report.summary or report.title,
                    highlights if isinstance(highlights, list) else [],
                    report.report_date,
                )
            except Exception:
                pass

        image_bytes = illus.generate_image_bytes(scene)
        if not image_bytes:
            fallback = self._fallback_illustration_url(organization_id, illus)
            if fallback and not force:
                logger.warning(
                    "Front photo generation failed; reusing previous illustration for org=%s",
                    organization_id,
                )
                self._link_report_illustration(report_id, organization_id, fallback)
                return fallback
            raise BadRequestError("이미지 생성에 실패했습니다.")

        url = illus.save_front_cache(organization_id, today, image_bytes)
        if report:
            # Keep a report-scoped copy too so report detail stays self-contained.
            report.illustration_url = illus.save_for_report(report.id, image_bytes)
            self.db.commit()
            return report.illustration_url
        return url

    def _clear_today_front_photo(
        self,
        organization_id: UUID,
        today: str,
        report_id: UUID | None,
        illus: ReportIllustrationService,
    ) -> None:
        path = illus.front_cache_path(organization_id, today)
        try:
            if path.is_file():
                path.unlink()
        except OSError as exc:
            logger.warning("Failed to clear front photo cache %s: %s", path, exc)

        if report_id is None:
            return
        report = self.db.scalar(
            select(DailyReport).where(
                DailyReport.id == report_id,
                DailyReport.organization_id == organization_id,
            )
        )
        if not report:
            return
        report.illustration_url = None
        self.db.commit()
        try:
            report_path = illus.reports_dir / f"{report_id}.png"
            if report_path.is_file():
                report_path.unlink()
        except OSError as exc:
            logger.warning("Failed to clear report illustration %s: %s", report_id, exc)

    def _fallback_illustration_url(
        self,
        organization_id: UUID,
        illus: ReportIllustrationService,
    ) -> str | None:
        cached = illus.latest_front_cache(organization_id)
        if cached:
            return cached
        return self.db.scalar(
            select(DailyReport.illustration_url)
            .where(
                DailyReport.organization_id == organization_id,
                DailyReport.illustration_url.is_not(None),
                DailyReport.illustration_url != "",
            )
            .order_by(DailyReport.report_date.desc())
            .limit(1)
        )

    def _link_report_illustration(
        self,
        report_id: UUID | None,
        organization_id: UUID,
        illustration_url: str,
    ) -> None:
        if report_id is None:
            return
        report = self.db.scalar(
            select(DailyReport).where(
                DailyReport.id == report_id,
                DailyReport.organization_id == organization_id,
            )
        )
        if report and not report.illustration_url:
            report.illustration_url = illustration_url
            self.db.commit()

    def _normalize_report_result(self, result: dict, target: date) -> dict:
        recs = result.get("recommendations") or result.get("key_changes") or []
        key_changes = []
        for rec in recs[:8]:
            why = (rec.get("why_read") or rec.get("description") or "").strip()
            key_changes.append(
                {
                    "title": (rec.get("title") or "").strip()[:120],
                    "description": why[:200],
                    "related_post_ids": rec.get("related_post_ids") or [],
                    "importance": rec.get("importance") or "medium",
                }
            )

        risks = result.get("risks")
        if isinstance(risks, list):
            risks = [r.strip() for r in risks if isinstance(r, str) and r.strip()][:2] or None
        else:
            risks = None

        action_items = result.get("action_items")
        if isinstance(action_items, list):
            action_items = [a.strip() for a in action_items if isinstance(a, str) and a.strip()][:2] or None
        else:
            action_items = None

        summary = (result.get("summary") or "").strip()[:400]
        return {
            "title": format_report_title(target),
            "summary": summary,
            "key_changes": key_changes,
            "risks": risks,
            "action_items": action_items,
        }

    def _create_empty_report(
        self, organization_id: UUID, target: date, *, edition_id: UUID | None = None
    ) -> DailyReportDetail:
        edition_name = None
        if edition_id:
            from app.services.edition_service import EditionService

            edition_name = EditionService(self.db).get(edition_id, organization_id).name
        report = DailyReport(
            organization_id=organization_id,
            edition_id=edition_id,
            report_date=target,
            title=format_report_title(target, edition_name),
            summary="모니터링 대상 소스를 확인했으나, 오늘은 새로운 변화가 없습니다.",
            model="none",
            prompt_version="v1",
        )
        self.db.add(report)
        self.db.commit()
        return self.get_report(report.id, organization_id)
