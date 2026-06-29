from __future__ import annotations

import logging
import re
from uuid import UUID

from app.core.config import get_settings
from app.search.es_client import get_es_client

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


def search_post_ids(
    organization_id: UUID,
    query: str,
    *,
    limit: int = 50,
    min_token_len: int = 1,
) -> list[UUID]:
    settings = get_settings()
    if not settings.search_uses_elasticsearch:
        return []

    client = get_es_client()
    if client is None:
        return []

    tokens = [t for t in _tokens(query) if len(t) >= min_token_len]
    if not tokens:
        return []

    should = []
    for token in tokens:
        should.append(
            {
                "multi_match": {
                    "query": token,
                    "fields": ["title^3", "summary^2", "body", "impact", "keyword_names"],
                    "type": "best_fields",
                    "operator": "and",
                }
            }
        )

    try:
        response = client.search(
            index=settings.elasticsearch_index_posts,
            size=limit,
            query={
                "bool": {
                    "filter": [
                        {"term": {"organization_id": str(organization_id)}},
                        {
                            "bool": {
                                "must_not": [{"term": {"status": "deleted"}}],
                            }
                        },
                    ],
                    "should": should,
                    "minimum_should_match": 1,
                }
            },
            sort=[{"collected_at": {"order": "desc"}}],
            _source=["post_id"],
        )
        ids: list[UUID] = []
        for hit in response.get("hits", {}).get("hits", []):
            source = hit.get("_source") or {}
            raw_id = source.get("post_id") or hit.get("_id")
            if raw_id:
                ids.append(UUID(str(raw_id)))
        return ids
    except Exception as exc:
        logger.warning("ES post search failed: %s", exc)
        return []


def search_post_ids_for_chat(
    organization_id: UUID,
    query: str,
    *,
    limit: int = 8,
    min_token_len: int = 2,
) -> list[UUID]:
    """BM25 + importance/recency boost for chatbot retrieval."""
    settings = get_settings()
    if not settings.search_uses_elasticsearch:
        return []

    client = get_es_client()
    if client is None:
        return []

    question = query.strip()
    if len(question) < min_token_len:
        return []

    try:
        response = client.search(
            index=settings.elasticsearch_index_posts,
            size=limit,
            query={
                "function_score": {
                    "query": {
                        "bool": {
                            "filter": [
                                {"term": {"organization_id": str(organization_id)}},
                                {
                                    "bool": {
                                        "must_not": [
                                            {"term": {"status": "deleted"}},
                                            {"term": {"status": "hidden"}},
                                        ]
                                    }
                                },
                            ],
                            "must": [
                                {
                                    "multi_match": {
                                        "query": question,
                                        "fields": [
                                            "title^3",
                                            "summary^2",
                                            "body",
                                            "impact",
                                            "keyword_names",
                                        ],
                                        "type": "best_fields",
                                        "operator": "or",
                                    }
                                }
                            ],
                        }
                    },
                    "functions": [
                        {"filter": {"term": {"importance": "high"}}, "weight": 2.0},
                        {"filter": {"term": {"importance": "medium"}}, "weight": 1.3},
                        {
                            "gauss": {
                                "collected_at": {
                                    "origin": "now",
                                    "scale": "30d",
                                    "offset": "2d",
                                    "decay": 0.5,
                                }
                            },
                            "weight": 1.5,
                        },
                    ],
                    "score_mode": "sum",
                    "boost_mode": "multiply",
                }
            },
            _source=["post_id"],
        )
        ids: list[UUID] = []
        for hit in response.get("hits", {}).get("hits", []):
            source = hit.get("_source") or {}
            raw_id = source.get("post_id") or hit.get("_id")
            if raw_id:
                ids.append(UUID(str(raw_id)))
        return ids
    except Exception as exc:
        logger.warning("ES chat search failed: %s", exc)
        return search_post_ids(
            organization_id,
            query,
            limit=limit,
            min_token_len=min_token_len,
        )
