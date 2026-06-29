from __future__ import annotations

import logging
import re
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.ai_output import AIOutput
from app.models.enums import PostStatus
from app.models.post import Post
from app.search.post_search import search_post_ids, search_post_ids_for_chat

logger = logging.getLogger(__name__)


def _tokens(query: str, *, max_tokens: int = 8) -> list[str]:
    parts = [t for t in re.split(r"[\s,?.!·]+", query.strip()) if t]
    tokens: list[str] = []
    seen: set[str] = set()

    def add(token: str) -> None:
        token = token.strip()
        if not token:
            return
        key = token.lower()
        if key in seen:
            return
        seen.add(key)
        tokens.append(token)

    add(query.strip())
    for part in parts:
        add(part)
        if len(tokens) >= max_tokens:
            break
    return tokens


def search_post_ids_postgres(
    db: Session,
    organization_id: UUID,
    query: str,
    *,
    limit: int = 50,
    min_token_len: int = 1,
) -> list[UUID]:
    tokens = [t for t in _tokens(query) if len(t) >= min_token_len]
    if not tokens:
        return []

    seen: set[UUID] = set()
    ordered: list[UUID] = []

    for token in tokens:
        like = f"%{token}%"
        rows = db.scalars(
            select(Post.id)
            .outerjoin(AIOutput)
            .where(
                Post.organization_id == organization_id,
                Post.status != PostStatus.deleted,
                or_(
                    Post.title.ilike(like),
                    Post.raw_content.ilike(like),
                    AIOutput.summary.ilike(like),
                ),
            )
            .order_by(Post.collected_at.desc())
            .limit(limit)
        ).all()
        for post_id in rows:
            if post_id not in seen:
                seen.add(post_id)
                ordered.append(post_id)
            if len(ordered) >= limit:
                return ordered
    return ordered


def _log_search_diff(query: str, es_ids: list[UUID], pg_ids: list[UUID]) -> None:
    es_set = set(es_ids)
    pg_set = set(pg_ids)
    only_es = es_set - pg_set
    only_pg = pg_set - es_set
    overlap = len(es_set & pg_set)
    logger.info(
        "search dual diff q=%r es=%s pg=%s overlap=%s only_es=%s only_pg=%s",
        query[:120],
        len(es_ids),
        len(pg_ids),
        overlap,
        len(only_es),
        len(only_pg),
    )
    if only_es or only_pg:
        logger.debug(
            "search dual diff detail only_es=%s only_pg=%s",
            [str(item) for item in list(only_es)[:5]],
            [str(item) for item in list(only_pg)[:5]],
        )


def resolve_search_post_ids(
    db: Session,
    organization_id: UUID,
    query: str,
    *,
    limit: int = 50,
    min_token_len: int = 1,
    chat: bool = False,
) -> list[UUID]:
    settings = get_settings()
    es_ids: list[UUID] = []
    pg_ids: list[UUID] = []

    if settings.search_uses_elasticsearch:
        if chat:
            es_ids = search_post_ids_for_chat(
                organization_id,
                query,
                limit=limit,
                min_token_len=min_token_len,
            )
        else:
            es_ids = search_post_ids(
                organization_id,
                query,
                limit=limit,
                min_token_len=min_token_len,
            )

    if settings.search_uses_postgres:
        pg_ids = search_post_ids_postgres(
            db,
            organization_id,
            query,
            limit=limit,
            min_token_len=min_token_len,
        )

    if settings.search_backend == "dual" and settings.search_dual_log_diff:
        _log_search_diff(query, es_ids, pg_ids)

    if settings.search_uses_elasticsearch and settings.search_backend in ("elasticsearch", "dual"):
        return es_ids
    return pg_ids
