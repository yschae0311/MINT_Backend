from __future__ import annotations

import logging
from functools import lru_cache

from app.core.config import get_settings
from app.search.es_client import get_es_client

logger = logging.getLogger(__name__)

_TEXT_FIELD_NAMES = ("title", "summary", "impact", "body", "source_name")


def _text_properties(analyzer: str) -> dict:
    props: dict = {
        "post_id": {"type": "keyword"},
        "organization_id": {"type": "keyword"},
        "keyword_names": {"type": "keyword"},
        "keyword_ids": {"type": "keyword"},
        "category": {"type": "keyword"},
        "importance": {"type": "keyword"},
        "board_type": {"type": "keyword"},
        "status": {"type": "keyword"},
        "source_id": {"type": "keyword"},
        "original_url": {"type": "keyword"},
        "collected_at": {"type": "date"},
        "published_at": {"type": "date"},
        "reliability_score": {"type": "integer"},
        "has_ai_summary": {"type": "boolean"},
        "indexed_at": {"type": "date"},
    }
    for name in _TEXT_FIELD_NAMES:
        if name == "title":
            props[name] = {
                "type": "text",
                "analyzer": analyzer,
                "fields": {"raw": {"type": "keyword"}},
            }
        else:
            props[name] = {"type": "text", "analyzer": analyzer}
    return props


def build_posts_index_body(*, use_nori: bool) -> dict:
    settings: dict = {
        "number_of_shards": 1,
        "number_of_replicas": 0,
    }
    analyzer = "korean" if use_nori else "standard"
    if use_nori:
        settings["analysis"] = {
            "tokenizer": {
                "nori_tokenizer": {
                    "type": "nori_tokenizer",
                    "decompound_mode": "mixed",
                }
            },
            "analyzer": {
                "korean": {
                    "type": "custom",
                    "tokenizer": "nori_tokenizer",
                    "filter": ["nori_readingform", "lowercase"],
                }
            },
        }
    return {
        "settings": settings,
        "mappings": {"properties": _text_properties(analyzer)},
    }


def nori_plugin_available(client) -> bool:
    try:
        plugins = client.cat.plugins(format="json")
        for row in plugins:
            component = (row.get("component") or row.get("name") or "").lower()
            if "nori" in component:
                return True
    except Exception as exc:
        logger.debug("Could not list ES plugins: %s", exc)
    return False


def resolve_use_nori(client) -> bool:
    settings = get_settings()
    mode = settings.elasticsearch_text_analyzer.strip().lower()
    if mode == "standard":
        return False
    if mode == "nori":
        return True
    return nori_plugin_available(client)


def ensure_posts_index() -> bool:
    """Create posts index if missing. Returns True when index is ready."""
    return _ensure_posts_index_cached()


@lru_cache
def _ensure_posts_index_cached() -> bool:
    settings = get_settings()
    client = get_es_client()
    if client is None:
        return False

    index = settings.elasticsearch_index_posts
    try:
        if client.indices.exists(index=index):
            return True

        use_nori = resolve_use_nori(client)
        body = build_posts_index_body(use_nori=use_nori)
        analyzer_label = "nori" if use_nori else "standard"
        try:
            client.indices.create(
                index=index,
                settings=body["settings"],
                mappings=body["mappings"],
            )
        except Exception as exc:
            if use_nori and "nori" in str(exc).lower():
                logger.warning(
                    "nori analyzer unavailable, falling back to standard: %s", exc
                )
                body = build_posts_index_body(use_nori=False)
                analyzer_label = "standard"
                client.indices.create(
                    index=index,
                    settings=body["settings"],
                    mappings=body["mappings"],
                )
            else:
                raise

        alias = settings.elasticsearch_index_posts_alias.strip()
        if alias and alias != index:
            client.indices.put_alias(index=index, name=alias)

        logger.info("Created Elasticsearch index %s (analyzer=%s)", index, analyzer_label)
        return True
    except Exception as exc:
        logger.error("Failed to ensure Elasticsearch index %s: %s", index, exc)
        return False
