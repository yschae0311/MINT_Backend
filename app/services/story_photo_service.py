"""Fill photos for ranked 1면 stories after the daily crawl and report."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.config import get_settings
from app.models.daily_report import DailyReport, DailyReportItem
from app.models.enums import KeywordScope, KeywordStatus, PostStatus
from app.models.personalization import Keyword, PostKeyword
from app.models.post import Post
from app.search.post_content import get_post_content
from app.services.article_image import extract_article_image_url
from app.services.report_illustration_service import ReportIllustrationService

logger = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")
TOP_STORY_PHOTO_COUNT = 10
_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; MINT/1.0; +https://mint.greenity.cloud) "
        "AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,image/avif,image/webp,image/*,*/*;q=0.8",
}
_MAX_IMAGE_BYTES = 4_000_000
_MIN_IMAGE_BYTES = 8_000


class StoryPhotoService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()
        self.illus = ReportIllustrationService()

    def ensure_ranked_photos(self, organization_id: UUID, *, report_date: date | None = None) -> int:
        from app.services.edition_service import EditionService

        target = report_date or datetime.now(KST).date()
        filled = 0
        for edition in EditionService(self.db).list_editions(organization_id, active_only=True):
            posts = self._ranked_posts(organization_id, edition.id, target)
            for post in posts:
                before = (post.image_url or "").strip()
                url = self.ensure_post_photo(post)
                if url and url != before:
                    filled += 1
                    self.db.commit()
        return filled

    def ensure_post_photo(self, post: Post, *, force: bool = False) -> str | None:
        if (post.image_url or "").strip() and not force:
            return post.image_url
        page_url = (post.original_url or "").strip()
        if page_url:
            html = self._fetch_html(page_url)
            if html:
                remote = extract_article_image_url(html, page_url)
                saved = self._save_remote_image(post.id, remote) if remote else None
                if saved:
                    post.image_url = saved
                    logger.info("Story photo from article post=%s", post.id)
                    return saved
        saved = self._save_ai_image(post)
        if saved:
            post.image_url = saved
            logger.info("Story photo from AI post=%s", post.id)
            return saved
        return (post.image_url or "").strip() or None

    def _ranked_posts(self, organization_id: UUID, edition_id: UUID, report_date: date) -> list[Post]:
        ordered: list[UUID] = []
        seen: set[UUID] = set()

        report = self.db.scalars(
            select(DailyReport)
            .where(
                DailyReport.organization_id == organization_id,
                DailyReport.edition_id == edition_id,
                DailyReport.report_date == report_date,
            )
            .order_by(DailyReport.created_at.desc())
        ).first()
        if report:
            item_ids = self.db.scalars(
                select(DailyReportItem.post_id)
                .where(DailyReportItem.report_id == report.id)
                .order_by(DailyReportItem.created_at.asc())
            ).all()
            for post_id in item_ids:
                if post_id not in seen:
                    seen.add(post_id)
                    ordered.append(post_id)

        for post_id in self._editorial_post_ids(organization_id, edition_id):
            if post_id not in seen:
                seen.add(post_id)
                ordered.append(post_id)
            if len(ordered) >= TOP_STORY_PHOTO_COUNT:
                break

        if not ordered:
            return []
        posts = self.db.scalars(
            select(Post)
            .options(joinedload(Post.source), joinedload(Post.ai_outputs))
            .where(Post.id.in_(ordered[:TOP_STORY_PHOTO_COUNT]))
        ).unique().all()
        by_id = {post.id: post for post in posts}
        return [by_id[post_id] for post_id in ordered[:TOP_STORY_PHOTO_COUNT] if post_id in by_id]

    def _editorial_post_ids(self, organization_id: UUID, edition_id: UUID) -> list[UUID]:
        from app.services.edition_service import EditionService
        from app.services.ev_display_filter import is_ev_related_post
        from app.services.topic_gate import load_topic_terms

        featured = EditionService(self.db).featured_keyword_ids(organization_id, edition_id)
        if not featured:
            featured = list(
                self.db.scalars(
                    select(Keyword.id).where(
                        Keyword.organization_id == organization_id,
                        Keyword.edition_id == edition_id,
                        Keyword.is_curated.is_(True),
                        Keyword.scope == KeywordScope.organization,
                        Keyword.status.in_([KeywordStatus.active, KeywordStatus.candidate]),
                    )
                ).all()
            )
        if not featured:
            return []

        days = self.settings.feed_window_days
        since = datetime.now(KST) - timedelta(days=days if days > 0 else 7)
        extra_terms = load_topic_terms(self.db, organization_id)
        posts = self.db.scalars(
            select(Post)
            .options(joinedload(Post.source))
            .join(PostKeyword, PostKeyword.post_id == Post.id)
            .where(
                Post.organization_id == organization_id,
                Post.status.not_in([PostStatus.deleted, PostStatus.hidden]),
                PostKeyword.keyword_id.in_(featured),
                Post.collected_at >= since,
            )
            .order_by(Post.collected_at.desc())
            .distinct()
            .limit(40)
        ).unique().all()
        ids: list[UUID] = []
        for post in posts:
            body = (post.raw_content or "")[:4000]
            if not is_ev_related_post(post, body=body, extra_terms=extra_terms):
                continue
            ids.append(post.id)
            if len(ids) >= TOP_STORY_PHOTO_COUNT:
                break
        return ids

    def _fetch_html(self, url: str) -> str | None:
        try:
            with httpx.Client(timeout=20.0, follow_redirects=True, headers=_FETCH_HEADERS) as client:
                response = client.get(url)
                response.raise_for_status()
                content_type = (response.headers.get("content-type") or "").lower()
                if "html" not in content_type and not response.text.lstrip().startswith("<"):
                    return None
                return response.text
        except Exception as exc:
            logger.debug("Story photo fetch failed url=%s: %s", url, exc)
            return None

    def _save_remote_image(self, post_id: UUID, url: str) -> str | None:
        try:
            with httpx.Client(timeout=25.0, follow_redirects=True, headers=_FETCH_HEADERS) as client:
                response = client.get(url)
                response.raise_for_status()
                data = response.content or b""
                content_type = (response.headers.get("content-type") or "").lower()
        except Exception as exc:
            logger.debug("Story photo download failed url=%s: %s", url, exc)
            return None
        if len(data) < _MIN_IMAGE_BYTES or len(data) > _MAX_IMAGE_BYTES:
            return None
        if "image" not in content_type and not data.startswith((b"\xff\xd8", b"\x89PNG", b"RIFF")):
            return None
        return self.illus.save_for_post(post_id, data)

    def _save_ai_image(self, post: Post) -> str | None:
        from app.services.bedrock_runtime import illustration_provider_ready
        from app.services.llm_client import get_llm_client

        if not illustration_provider_ready(self.settings):
            return None
        content = get_post_content(self.db, post.id)
        summary = (content.summary or "").strip()
        scene = post.title
        try:
            scene = get_llm_client().generate_story_illustration_scene(post.title, summary)
        except Exception as exc:
            logger.debug("Story photo scene fallback post=%s: %s", post.id, exc)
        image_bytes = self.illus.generate_image_bytes(scene)
        if not image_bytes:
            return None
        return self.illus.save_for_post(post.id, image_bytes)
