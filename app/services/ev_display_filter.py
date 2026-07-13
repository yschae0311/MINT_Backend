"""Display-layer EV relevance filter for already-stored posts."""
from __future__ import annotations

from typing import Any

from app.services.ev_relevance import has_strong_ev_signal, is_obvious_junk


def is_ev_related_post(post: Any, *, body: str = "") -> bool:
    """Return True if a stored post should appear on user-facing news/dashboard surfaces.

    Uses title + body (+ url) only. Taxonomy category names like "충전 인프라"
    must NOT count as EV signal by themselves.
    """
    title = (getattr(post, "title", None) or "").strip()
    url = ""
    source = getattr(post, "source", None)
    if source is not None and getattr(source, "url", None):
        url = source.url or ""
    original = getattr(post, "original_url", None) or ""
    if original:
        url = original

    text = body or getattr(post, "raw_content", "") or ""
    if is_obvious_junk(title, text, url):
        return False

    return has_strong_ev_signal(title, text, url)
