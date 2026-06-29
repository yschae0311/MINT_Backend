from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.enums import SearchIndexAction
from app.models.post import Post
from app.models.search_index_queue import SearchIndexQueue
from app.search.post_content import PostContent, delete_post_content, save_post_content, sync_post_metadata

logger = logging.getLogger(__name__)

_MAX_ATTEMPTS = 8


def content_to_payload(content: PostContent) -> dict[str, Any]:
    return {
        "original_url": content.original_url,
        "summary": content.summary,
        "impact": content.impact,
        "body": content.body,
        "action_items": content.action_items,
    }


def enqueue_search_index(
    db: Session,
    post_id: UUID,
    action: SearchIndexAction,
    *,
    payload: dict[str, Any] | None = None,
) -> None:
    if not get_settings().search_uses_elasticsearch:
        return

    db.add(
        SearchIndexQueue(
            post_id=post_id,
            action=action,
            payload=payload,
        )
    )
    db.flush()

    try:
        from app.workers.tasks import process_search_index_queue_task

        process_search_index_queue_task.delay()
    except Exception as exc:
        logger.debug("Could not dispatch search index queue task: %s", exc)


def _apply_index(db: Session, post_id: UUID, payload: dict[str, Any] | None) -> bool:
    post = db.get(Post, post_id)
    if not post:
        return True

    if payload:
        return save_post_content(
            db,
            post,
            original_url=payload.get("original_url"),
            summary=payload.get("summary"),
            impact=payload.get("impact"),
            body=payload.get("body"),
            action_items=payload.get("action_items"),
            merge_existing=True,
        )
    return sync_post_metadata(db, post)


def process_search_index_queue(db: Session, *, batch_size: int = 50) -> tuple[int, int]:
    """Process pending outbox rows. Returns (processed_ok, failed)."""
    if not get_settings().search_uses_elasticsearch:
        return 0, 0

    rows = list(
        db.scalars(
            select(SearchIndexQueue)
            .where(SearchIndexQueue.processed_at.is_(None))
            .order_by(SearchIndexQueue.created_at.asc())
            .limit(batch_size)
        ).all()
    )
    if not rows:
        return 0, 0

    ok = 0
    failed = 0
    now = datetime.now(timezone.utc)

    for row in rows:
        try:
            if row.action == SearchIndexAction.delete:
                success = delete_post_content(row.post_id)
            else:
                success = _apply_index(db, row.post_id, row.payload)

            if success:
                row.processed_at = now
                row.last_error = None
                ok += 1
            else:
                row.attempts += 1
                row.last_error = "elasticsearch write returned false"
                if row.attempts >= _MAX_ATTEMPTS:
                    row.processed_at = now
                    logger.error(
                        "search index outbox gave up post=%s action=%s attempts=%s",
                        row.post_id,
                        row.action.value,
                        row.attempts,
                    )
                failed += 1
        except Exception as exc:
            row.attempts += 1
            row.last_error = str(exc)[:2000]
            if row.attempts >= _MAX_ATTEMPTS:
                row.processed_at = now
            failed += 1
            logger.warning(
                "search index outbox failed post=%s action=%s: %s",
                row.post_id,
                row.action.value,
                exc,
            )

    db.commit()
    return ok, failed


def pending_search_index_count(db: Session) -> int:
    from sqlalchemy import func

    return (
        db.scalar(
            select(func.count())
            .select_from(SearchIndexQueue)
            .where(SearchIndexQueue.processed_at.is_(None))
        )
        or 0
    )
