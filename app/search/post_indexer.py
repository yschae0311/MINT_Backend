from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.models.post import Post
from app.search.post_content import delete_post_content, save_post_content, sync_post_metadata


def index_post(db: Session, post_id: UUID) -> bool:
    post = db.get(Post, post_id)
    if not post:
        return False
    return sync_post_metadata(db, post)


def delete_post_index(post_id: UUID) -> bool:
    return delete_post_content(post_id)


def persist_new_post_content(
    db: Session,
    post: Post,
    *,
    original_url: str | None = None,
    summary: str | None = None,
    impact: str | None = None,
    body: str | None = None,
    action_items: list | None = None,
) -> bool:
    return save_post_content(
        db,
        post,
        original_url=original_url,
        summary=summary,
        impact=impact,
        body=body,
        action_items=action_items,
        merge_existing=False,
    )
