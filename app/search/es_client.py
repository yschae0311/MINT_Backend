from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from app.core.config import get_settings

if TYPE_CHECKING:
    from elasticsearch import AsyncElasticsearch, Elasticsearch

logger = logging.getLogger(__name__)

_BACKEND_ROOT = Path(__file__).resolve().parents[2]

_CA_FILE_CANDIDATES = (
    "http_ca.crt",
    "ca.crt",
    "elastic-ca.pem",
    "elasticsearch-ca.pem",
    "root-ca.pem",
)


def resolve_ca_certs_path(raw: str) -> Path | None:
    value = (raw or "").strip()
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        path = _BACKEND_ROOT / path
    if path.is_file():
        return path
    if path.is_dir():
        return find_ca_in_directory(path)
    return None


def find_ca_in_directory(directory: Path) -> Path | None:
    for name in _CA_FILE_CANDIDATES:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    for pattern in ("*.crt", "*.pem"):
        for candidate in sorted(directory.glob(pattern)):
            if candidate.is_file():
                return candidate
    return None


def _es_connection_kwargs() -> dict | None:
    """ES_ADDR + CA_CRT + basic_auth — 동료 환경과 동일한 연결 옵션."""
    settings = get_settings()
    url = settings.elasticsearch_url.strip()
    if not url:
        return None

    kwargs: dict = {
        "hosts": [url],
        "request_timeout": settings.elasticsearch_request_timeout_sec,
        "verify_certs": settings.elasticsearch_verify_certs,
    }

    if settings.elasticsearch_username:
        kwargs["basic_auth"] = (
            settings.elasticsearch_username,
            settings.elasticsearch_password,
        )

    ca_path = resolve_ca_certs_path(settings.elasticsearch_ca_certs)
    if ca_path:
        kwargs["ca_certs"] = str(ca_path)
    elif settings.elasticsearch_verify_certs and url.lower().startswith("https://"):
        logger.warning(
            "Elasticsearch HTTPS without ELASTICSEARCH_CA_CERTS — TLS verify may fail"
        )

    return kwargs


def _https_requires_ca() -> str | None:
    settings = get_settings()
    url = settings.elasticsearch_url.strip()
    if not url.lower().startswith("https://"):
        return None
    if not settings.elasticsearch_verify_certs:
        return None
    if resolve_ca_certs_path(settings.elasticsearch_ca_certs):
        return None
    return "HTTPS requires ELASTICSEARCH_CA_CERTS (extract ca_certs.zip)"


@lru_cache
def get_es_client() -> Elasticsearch | None:
    """Celery·동기 인덱싱용."""
    kwargs = _es_connection_kwargs()
    if kwargs is None:
        return None
    from elasticsearch import Elasticsearch

    return Elasticsearch(**kwargs)


@lru_cache
def get_async_es_client() -> AsyncElasticsearch | None:
    """API·비동기 작업용 — AsyncElasticsearch(ES_ADDR, ca_certs=..., basic_auth=...)."""
    kwargs = _es_connection_kwargs()
    if kwargs is None:
        return None
    from elasticsearch import AsyncElasticsearch

    return AsyncElasticsearch(**kwargs)


async def ping_elasticsearch() -> tuple[str, str | None]:
    """Returns (status, detail). status: ok | disabled | error"""
    settings = get_settings()
    if not settings.elasticsearch_url.strip():
        return "disabled", None

    ca_error = _https_requires_ca()
    if ca_error:
        return "error", ca_error

    client = get_async_es_client()
    if client is None:
        return "disabled", None

    try:
        if not await client.ping():
            return "error", "ping returned false"
        info = await client.info()
        version = info.get("version", {}).get("number", "unknown")
        return "ok", version
    except Exception as exc:
        logger.warning("Elasticsearch ping failed: %s", exc)
        return "error", str(exc)


def ping_elasticsearch_sync() -> tuple[str, str | None]:
    """CLI·동기 컨텍스트용."""
    settings = get_settings()
    if not settings.elasticsearch_url.strip():
        return "disabled", None

    ca_error = _https_requires_ca()
    if ca_error:
        return "error", ca_error

    client = get_es_client()
    if client is None:
        return "disabled", None

    try:
        if not client.ping():
            return "error", "ping returned false"
        info = client.info()
        version = info.get("version", {}).get("number", "unknown")
        return "ok", version
    except Exception as exc:
        logger.warning("Elasticsearch ping failed: %s", exc)
        return "error", str(exc)


async def close_async_es_client() -> None:
    client = get_async_es_client()
    if client is not None:
        await client.close()
    reset_es_client_cache()


def reset_es_client_cache() -> None:
    get_es_client.cache_clear()
    get_async_es_client.cache_clear()
