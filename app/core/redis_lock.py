from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import redis

from app.core.config import get_settings


@contextmanager
def redis_lock(key: str, *, ttl_seconds: int = 120) -> Iterator[bool]:
    """Best-effort distributed lock. Yields True when acquired."""
    client = redis.from_url(get_settings().redis_url)
    acquired = bool(client.set(key, "1", nx=True, ex=ttl_seconds))
    try:
        yield acquired
    finally:
        if acquired:
            client.delete(key)
