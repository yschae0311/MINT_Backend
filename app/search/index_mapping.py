from __future__ import annotations

import logging

from app.core.config import get_settings
from app.search.es_client import get_es_client

logger = logging.getLogger(__name__)

POSTS_INDEX_BODY = {
    "settings": {
        "analysis": {
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
        },
        "number_of_shards": 1,
        "number_of_replicas": 0,
    },
    "mappings": {
        "properties": {
            "post_id": {"type": "keyword"},
            "organization_id": {"type": "keyword"},
            "title": {
                "type": "text",
                "analyzer": "korean",
                "fields": {"raw": {"type": "keyword"}},
            },
            "summary": {"type": "text", "analyzer": "korean"},
            "impact": {"type": "text", "analyzer": "korean"},
            "body": {"type": "text", "analyzer": "korean"},
            "keyword_names": {"type": "keyword"},
            "keyword_ids": {"type": "keyword"},
            "category": {"type": "keyword"},
            "importance": {"type": "keyword"},
            "board_type": {"type": "keyword"},
            "status": {"type": "keyword"},
            "source_id": {"type": "keyword"},
            "source_name": {"type": "text", "analyzer": "korean"},
            "original_url": {"type": "keyword"},
            "collected_at": {"type": "date"},
            "published_at": {"type": "date"},
            "reliability_score": {"type": "integer"},
            "has_ai_summary": {"type": "boolean"},
            "indexed_at": {"type": "date"},
        }
    },
}


def ensure_posts_index() -> bool:
    """Create posts index if missing. Returns True when index is ready."""
    settings = get_settings()
    client = get_es_client()
    if client is None:
        return False

    index = settings.elasticsearch_index_posts
    try:
        if client.indices.exists(index=index):
            return True
        client.indices.create(
            index=index,
            settings=POSTS_INDEX_BODY["settings"],
            mappings=POSTS_INDEX_BODY["mappings"],
        )
        alias = settings.elasticsearch_index_posts_alias.strip()
        if alias and alias != index:
            client.indices.put_alias(index=index, name=alias)
        logger.info("Created Elasticsearch index %s", index)
        return True
    except Exception as exc:
        logger.error("Failed to ensure Elasticsearch index %s: %s", index, exc)
        return False
