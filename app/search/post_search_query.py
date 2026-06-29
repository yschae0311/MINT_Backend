from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.config import get_settings
from app.models.post import Post
from app.search.es_client import get_es_client

logger = logging.getLogger(__name__)

_HIGHLIGHT_PRE = "<em>"
_HIGHLIGHT_POST = "</em>"


@dataclass
class PostSearchFilters:
    organization_id: UUID
    query: str | None = None
    board_type: str | None = None
    status: str | None = None
    exclude_statuses: list[str] = field(default_factory=lambda: ["deleted"])
    category: str | None = None
    importance: str | None = None
    source_id: UUID | None = None
    keyword_ids: list[UUID] | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None


@dataclass
class PostSearchHit:
    post_id: UUID
    title: str
    summary: str | None = None
    original_url: str | None = None
    board_type: str | None = None
    source_name: str | None = None
    category: str | None = None
    importance: str | None = None
    collected_at: datetime | None = None
    highlight_title: str | None = None
    highlight_summary: str | None = None
    score: float | None = None


@dataclass
class PostSearchResult:
    hits: list[PostSearchHit]
    total: int


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


def _build_filters(filters: PostSearchFilters) -> list[dict[str, Any]]:
    clauses: list[dict[str, Any]] = [
        {"term": {"organization_id": str(filters.organization_id)}},
    ]
    if filters.board_type:
        clauses.append({"term": {"board_type": filters.board_type}})
    if filters.status:
        clauses.append({"term": {"status": filters.status}})
    elif filters.exclude_statuses:
        clauses.append(
            {
                "bool": {
                    "must_not": [{"term": {"status": status}} for status in filters.exclude_statuses],
                }
            }
        )
    if filters.category:
        clauses.append({"term": {"category": filters.category}})
    if filters.importance:
        clauses.append({"term": {"importance": filters.importance}})
    if filters.source_id:
        clauses.append({"term": {"source_id": str(filters.source_id)}})
    if filters.keyword_ids:
        clauses.append(
            {
                "terms": {
                    "keyword_ids": [str(keyword_id) for keyword_id in filters.keyword_ids],
                }
            }
        )
    if filters.date_from or filters.date_to:
        range_body: dict[str, str] = {}
        if filters.date_from:
            range_body["gte"] = filters.date_from.isoformat()
        if filters.date_to:
            range_body["lt"] = filters.date_to.isoformat()
        clauses.append({"range": {"collected_at": range_body}})
    return clauses


def _build_text_clause(query: str) -> dict[str, Any]:
    tokens = _tokens(query)
    if not tokens:
        return {}
    should = [
        {
            "multi_match": {
                "query": token,
                "fields": ["title^3", "summary^2", "body", "impact", "keyword_names"],
                "type": "best_fields",
                "operator": "and",
            }
        }
        for token in tokens
    ]
    return {"bool": {"should": should, "minimum_should_match": 1}}


def _pick_highlight(hit: dict[str, Any]) -> tuple[str | None, str | None]:
    highlight = hit.get("highlight") or {}
    title_parts = highlight.get("title") or []
    summary_parts = highlight.get("summary") or highlight.get("body") or []
    return (
        title_parts[0] if title_parts else None,
        summary_parts[0] if summary_parts else None,
    )


def _parse_hit(hit: dict[str, Any]) -> PostSearchHit | None:
    source = hit.get("_source") or {}
    raw_id = source.get("post_id") or hit.get("_id")
    if not raw_id:
        return None
    highlight_title, highlight_summary = _pick_highlight(hit)
    collected_raw = source.get("collected_at")
    collected_at = None
    if collected_raw:
        try:
            collected_at = datetime.fromisoformat(str(collected_raw).replace("Z", "+00:00"))
        except ValueError:
            collected_at = None
    return PostSearchHit(
        post_id=UUID(str(raw_id)),
        title=source.get("title") or "",
        summary=source.get("summary") or None,
        original_url=source.get("original_url") or None,
        board_type=source.get("board_type"),
        source_name=source.get("source_name") or None,
        category=source.get("category") or None,
        importance=source.get("importance"),
        collected_at=collected_at,
        highlight_title=highlight_title,
        highlight_summary=highlight_summary,
        score=hit.get("_score"),
    )


def search_posts(
    filters: PostSearchFilters,
    *,
    page: int = 1,
    size: int = 20,
    highlight: bool = False,
) -> PostSearchResult | None:
    """Single ES query: filters + optional text + pagination + highlight."""
    settings = get_settings()
    if not settings.search_uses_elasticsearch:
        return None

    client = get_es_client()
    if client is None:
        return None

    page = max(1, page)
    size = max(1, min(size, 100))
    from_offset = (page - 1) * size

    bool_query: dict[str, Any] = {"filter": _build_filters(filters)}
    text = (filters.query or "").strip()
    if text:
        bool_query["must"] = [_build_text_clause(text)]

    body: dict[str, Any] = {
        "query": {"bool": bool_query},
        "from": from_offset,
        "size": size,
        "track_total_hits": True,
    }
    if text:
        body["sort"] = [{"_score": "desc"}, {"collected_at": {"order": "desc"}}]
    else:
        body["sort"] = [{"collected_at": {"order": "desc"}}]

    if highlight and text:
        body["highlight"] = {
            "pre_tags": [_HIGHLIGHT_PRE],
            "post_tags": [_HIGHLIGHT_POST],
            "fields": {
                "title": {"fragment_size": 140, "number_of_fragments": 1},
                "summary": {"fragment_size": 220, "number_of_fragments": 1},
                "body": {"fragment_size": 220, "number_of_fragments": 1},
            },
        }

    try:
        response = client.search(index=settings.elasticsearch_index_posts, **body)
        hits = []
        for hit in response.get("hits", {}).get("hits", []):
            parsed = _parse_hit(hit)
            if parsed:
                hits.append(parsed)
        total_raw = response.get("hits", {}).get("total")
        if isinstance(total_raw, dict):
            total = int(total_raw.get("value", 0))
        else:
            total = int(total_raw or 0)
        return PostSearchResult(hits=hits, total=total)
    except Exception as exc:
        logger.warning("ES search_posts failed: %s", exc)
        return None


def load_posts_ordered(db: Session, post_ids: list[UUID]) -> list[Post]:
    if not post_ids:
        return []
    posts = list(
        db.scalars(
            select(Post)
            .options(joinedload(Post.source), joinedload(Post.ai_outputs))
            .where(Post.id.in_(post_ids))
        ).unique().all()
    )
    by_id = {post.id: post for post in posts}
    return [by_id[post_id] for post_id in post_ids if post_id in by_id]


def strip_highlight_markup(value: str | None) -> str | None:
    if not value:
        return None
    return re.sub(r"</?em>", "", value)
