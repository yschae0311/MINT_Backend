#!/usr/bin/env python3
"""Quick Elasticsearch connectivity check (uses .env)."""

import asyncio

from app.core.config import get_settings
from app.search.es_client import ping_elasticsearch, resolve_ca_certs_path
from app.search.index_mapping import ensure_posts_index


async def main() -> None:
    settings = get_settings()
    print(f"search_backend={settings.search_backend}")
    print(f"elasticsearch_url={settings.elasticsearch_url or '(empty)'}")
    ca = resolve_ca_certs_path(settings.elasticsearch_ca_certs)
    print(f"elasticsearch_ca_certs={ca or '(not set)'}")

    status, detail = await ping_elasticsearch()
    print(f"ping: {status}" + (f" ({detail})" if detail else ""))

    if settings.search_uses_elasticsearch and status == "ok":
        ready = ensure_posts_index()
        print(f"index {settings.elasticsearch_index_posts}: {'ready' if ready else 'failed'}")


if __name__ == "__main__":
    asyncio.run(main())
