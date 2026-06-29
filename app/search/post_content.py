from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.config import get_settings
from app.models.ai_output import AIOutput
from app.models.enums import KeywordStatus, SearchIndexAction
from app.models.personalization import Keyword, PostKeyword
from app.models.post import Post
from app.search.es_client import get_es_client
from app.search.index_mapping import ensure_posts_index

logger = logging.getLogger(__name__)

# PG ai_outputs.summary is NOT NULL — text lives in Elasticsearch.
_PG_AI_SUMMARY_PLACEHOLDER = " "


@dataclass
class PostContent:
    original_url: str | None = None
    summary: str | None = None
    impact: str | None = None
    body: str = ""
    action_items: list | None = None

    @property
    def has_summary(self) -> bool:
        return bool((self.summary or "").strip())


def clear_pg_text_fields(post: Post) -> None:
    post.original_url = None
    post.raw_content = ""


def pg_ai_summary_placeholder() -> str:
    return _PG_AI_SUMMARY_PLACEHOLDER


def _content_from_legacy_post(post: Post) -> PostContent:
    latest_ai: AIOutput | None = None
    if post.ai_outputs:
        latest_ai = max(post.ai_outputs, key=lambda item: item.created_at)
    summary = None
    if latest_ai and (latest_ai.summary or "").strip() not in ("", _PG_AI_SUMMARY_PLACEHOLDER):
        summary = latest_ai.summary
    return PostContent(
        original_url=post.original_url,
        summary=summary,
        impact=latest_ai.impact if latest_ai else None,
        body=(post.raw_content or "")[:8000],
        action_items=latest_ai.action_items if latest_ai else None,
    )


def _fetch_es_document(post_id: UUID) -> dict[str, Any] | None:
    settings = get_settings()
    if not settings.search_uses_elasticsearch:
        return None
    client = get_es_client()
    if client is None:
        return None
    try:
        response = client.get(
            index=settings.elasticsearch_index_posts,
            id=str(post_id),
            ignore=[404],
        )
        if not response or not response.get("found"):
            return None
        return response.get("_source") or {}
    except Exception as exc:
        logger.debug("ES get post %s failed: %s", post_id, exc)
        return None


def _document_from_source(source: dict[str, Any]) -> PostContent:
    return PostContent(
        original_url=source.get("original_url") or None,
        summary=source.get("summary") or None,
        impact=source.get("impact") or None,
        body=(source.get("body") or "")[:8000],
        action_items=source.get("action_items"),
    )


def get_post_content(db: Session, post_id: UUID, *, fallback_legacy: bool = True) -> PostContent:
    doc = _fetch_es_document(post_id)
    if doc:
        return _document_from_source(doc)

    if not fallback_legacy:
        return PostContent()

    post = db.scalars(
        select(Post)
        .options(joinedload(Post.ai_outputs))
        .where(Post.id == post_id)
    ).unique().first()
    if not post:
        return PostContent()
    return _content_from_legacy_post(post)


def mget_post_contents(
    db: Session, post_ids: list[UUID], *, fallback_legacy: bool = True
) -> dict[UUID, PostContent]:
    settings = get_settings()
    results: dict[UUID, PostContent] = {}
    missing: list[UUID] = []

    if settings.search_uses_elasticsearch and post_ids:
        client = get_es_client()
        if client is not None:
            try:
                response = client.mget(
                    index=settings.elasticsearch_index_posts,
                    ids=[str(pid) for pid in post_ids],
                )
                for item in response.get("docs", []):
                    pid = UUID(item["_id"])
                    if item.get("found") and item.get("_source"):
                        results[pid] = _document_from_source(item["_source"])
                    else:
                        missing.append(pid)
            except Exception as exc:
                logger.warning("ES mget failed: %s", exc)
                missing = list(post_ids)
        else:
            missing = list(post_ids)
    else:
        missing = list(post_ids)

    if fallback_legacy and missing:
        posts = db.scalars(
            select(Post)
            .options(joinedload(Post.ai_outputs))
            .where(Post.id.in_(missing))
        ).unique().all()
        for post in posts:
            results[post.id] = _content_from_legacy_post(post)

    for pid in post_ids:
        results.setdefault(pid, PostContent())
    return results


def find_post_id_by_url(organization_id: UUID, url: str) -> UUID | None:
    settings = get_settings()
    if not settings.search_uses_elasticsearch:
        return None
    client = get_es_client()
    if client is None:
        return None

    raw = (url or "").strip()
    if not raw:
        return None

    from app.services.post_dedup import normalize_crawl_url

    norm = normalize_crawl_url(raw)
    urls = [u for u in {raw, norm} if u]
    try:
        response = client.search(
            index=settings.elasticsearch_index_posts,
            size=1,
            query={
                "bool": {
                    "filter": [
                        {"term": {"organization_id": str(organization_id)}},
                        {"terms": {"original_url": urls}},
                    ]
                }
            },
        )
        hits = response.get("hits", {}).get("hits", [])
        if hits:
            return UUID(hits[0]["_source"]["post_id"])
    except Exception as exc:
        logger.debug("ES url lookup failed: %s", exc)
    return None


def _build_index_document(
    db: Session,
    post: Post,
    content: PostContent,
) -> dict[str, Any]:
    keyword_rows = db.execute(
        select(PostKeyword, Keyword)
        .join(Keyword, Keyword.id == PostKeyword.keyword_id)
        .where(
            PostKeyword.post_id == post.id,
            Keyword.status != KeywordStatus.archived,
        )
    ).all()

    return {
        "post_id": str(post.id),
        "organization_id": str(post.organization_id),
        "title": post.title or "",
        "summary": content.summary or "",
        "impact": content.impact or "",
        "body": (content.body or "")[:8000],
        "action_items": content.action_items,
        "keyword_names": [keyword.name for _, keyword in keyword_rows],
        "keyword_ids": [str(keyword.id) for _, keyword in keyword_rows],
        "category": post.category or "",
        "importance": post.importance.value,
        "board_type": post.board_type.value,
        "status": post.status.value,
        "source_id": str(post.source_id) if post.source_id else None,
        "source_name": post.source.name if post.source else "",
        "original_url": content.original_url or "",
        "collected_at": post.collected_at,
        "published_at": post.published_at,
        "reliability_score": post.reliability_score,
        "has_ai_summary": content.has_summary,
        "indexed_at": datetime.now(timezone.utc),
    }


def save_post_content(
    db: Session,
    post: Post,
    *,
    original_url: str | None = None,
    summary: str | None = None,
    impact: str | None = None,
    body: str | None = None,
    action_items: list | None = None,
    merge_existing: bool = True,
) -> bool:
    """Upsert article URL/summary/body into Elasticsearch (canonical text store)."""
    settings = get_settings()
    if not settings.search_uses_elasticsearch:
        if original_url is not None:
            post.original_url = original_url or None
        if body is not None:
            post.raw_content = body or ""
        return False

    if merge_existing:
        base = get_post_content(db, post.id, fallback_legacy=True)
    else:
        base = PostContent()

    content = PostContent(
        original_url=original_url if original_url is not None else base.original_url,
        summary=summary if summary is not None else base.summary,
        impact=impact if impact is not None else base.impact,
        body=body if body is not None else base.body,
        action_items=action_items if action_items is not None else base.action_items,
    )

    client = get_es_client()
    if client is None or not ensure_posts_index():
        from app.search.index_outbox import content_to_payload, enqueue_search_index

        enqueue_search_index(
            db,
            post.id,
            SearchIndexAction.index,
            payload=content_to_payload(content),
        )
        return False

    if post.source is None and post.source_id:
        db.refresh(post, attribute_names=["source"])

    doc = _build_index_document(db, post, content)
    try:
        client.index(
            index=settings.elasticsearch_index_posts,
            id=str(post.id),
            document=doc,
        )
        clear_pg_text_fields(post)
        return True
    except Exception as exc:
        logger.warning("Failed to save post content to ES %s: %s", post.id, exc)
        from app.search.index_outbox import content_to_payload, enqueue_search_index

        enqueue_search_index(
            db,
            post.id,
            SearchIndexAction.index,
            payload=content_to_payload(content),
        )
        return False


def sync_post_metadata(db: Session, post: Post) -> bool:
    """Re-index ES document after PG metadata-only changes (status, category, etc.)."""
    content = get_post_content(db, post.id, fallback_legacy=True)
    return save_post_content(
        db,
        post,
        original_url=content.original_url,
        summary=content.summary,
        impact=content.impact,
        body=content.body,
        action_items=content.action_items,
        merge_existing=False,
    )


def delete_post_content(post_id: UUID, db: Session | None = None) -> bool:
    settings = get_settings()
    if not settings.search_uses_elasticsearch:
        return False
    client = get_es_client()
    if client is None:
        if db is not None:
            from app.search.index_outbox import enqueue_search_index

            enqueue_search_index(db, post_id, SearchIndexAction.delete)
        return False
    try:
        client.delete(
            index=settings.elasticsearch_index_posts,
            id=str(post_id),
            ignore=[404],
        )
        return True
    except Exception as exc:
        logger.warning("Failed to delete ES post %s: %s", post_id, exc)
        if db is not None:
            from app.search.index_outbox import enqueue_search_index

            enqueue_search_index(db, post_id, SearchIndexAction.delete)
        return False
