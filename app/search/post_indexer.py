from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.search.es_client import get_es_client
from app.search.index_mapping import ensure_posts_index
from app.search.post_document import build_post_document

logger = logging.getLogger(__name__)


def index_post(db: Session, post_id: UUID) -> bool:
    settings = get_settings()
    if not settings.search_uses_elasticsearch:
        return False

    client = get_es_client()
    if client is None:
        return False

    if not ensure_posts_index():
        return False

    doc = build_post_document(db, post_id)
    if doc is None:
        return False

    index = settings.elasticsearch_index_posts
    try:
        client.index(index=index, id=str(post_id), document=doc)
        return True
    except Exception as exc:
        logger.warning("Failed to index post %s: %s", post_id, exc)
        return False


def delete_post_index(post_id: UUID) -> bool:
    settings = get_settings()
    if not settings.search_uses_elasticsearch:
        return False

    client = get_es_client()
    if client is None:
        return False

    index = settings.elasticsearch_index_posts
    try:
        client.delete(index=index, id=str(post_id), ignore_status=[404])
        return True
    except Exception as exc:
        logger.warning("Failed to delete post index %s: %s", post_id, exc)
        return False
