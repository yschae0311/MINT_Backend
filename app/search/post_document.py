from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models.ai_output import AIOutput
from app.models.enums import KeywordStatus
from app.models.personalization import Keyword, PostKeyword
from app.models.post import Post


def build_post_document(db: Session, post_id: UUID) -> dict | None:
    post = db.scalars(
        select(Post)
        .options(joinedload(Post.source), joinedload(Post.ai_outputs))
        .where(Post.id == post_id)
    ).unique().first()
    if not post:
        return None

    latest_ai: AIOutput | None = None
    if post.ai_outputs:
        latest_ai = max(post.ai_outputs, key=lambda item: item.created_at)

    keyword_rows = db.execute(
        select(PostKeyword, Keyword)
        .join(Keyword, Keyword.id == PostKeyword.keyword_id)
        .where(
            PostKeyword.post_id == post.id,
            Keyword.status != KeywordStatus.archived,
        )
    ).all()

    keyword_names = [keyword.name for _, keyword in keyword_rows]
    keyword_ids = [str(keyword.id) for _, keyword in keyword_rows]

    return {
        "post_id": str(post.id),
        "organization_id": str(post.organization_id),
        "title": post.title or "",
        "summary": (latest_ai.summary if latest_ai else "") or "",
        "impact": (latest_ai.impact if latest_ai else "") or "",
        "body": (post.raw_content or "")[:8000],
        "keyword_names": keyword_names,
        "keyword_ids": keyword_ids,
        "category": post.category or "",
        "importance": post.importance.value,
        "board_type": post.board_type.value,
        "status": post.status.value,
        "source_id": str(post.source_id) if post.source_id else None,
        "source_name": post.source.name if post.source else "",
        "original_url": post.original_url or "",
        "collected_at": post.collected_at,
        "published_at": post.published_at,
        "reliability_score": post.reliability_score,
        "has_ai_summary": bool(latest_ai and latest_ai.summary),
        "indexed_at": datetime.now(timezone.utc),
    }
