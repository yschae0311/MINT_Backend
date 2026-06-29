#!/usr/bin/env python3
"""Quick Elasticsearch connectivity check (uses .env)."""

import asyncio

from app.core.config import get_settings
from app.search.es_client import close_async_es_client, ping_elasticsearch, resolve_ca_certs_path
from app.search.index_mapping import ensure_posts_index


async def main() -> None:
    settings = get_settings()
    print(f"search_backend={settings.search_backend}")
    print(f"elasticsearch_url={settings.elasticsearch_url or '(empty)'}")
    ca = resolve_ca_certs_path(settings.elasticsearch_ca_certs)
    print(f"elasticsearch_ca_certs={ca or '(not set)'}")

    try:
        status, detail = await ping_elasticsearch()
        print(f"ping: {status}" + (f" ({detail})" if detail else ""))

        if status == "error" and settings.elasticsearch_url.startswith("https://172.31."):
            print(
                "hint: 172.31.x.x is a VPC private address — use VPN/SSH tunnel from your Mac, "
                "or run es_ping on the EC2 instance in the same VPC."
            )

        if settings.search_uses_elasticsearch and status == "ok":
            ready = ensure_posts_index()
            label = "ready" if ready else "failed"
            print(f"index {settings.elasticsearch_index_posts}: {label}")
            if not ready:
                print(
                    "hint: managed ES without nori plugin — set ELASTICSEARCH_TEXT_ANALYZER=standard "
                    "or delete the index and retry."
                )
    finally:
        await close_async_es_client()


if __name__ == "__main__":
    asyncio.run(main())
