"""Normalize crawl URLs/titles and detect duplicate posts."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.enums import PostStatus
from app.models.post import Post
from app.services.personalization_service import normalize_keyword

_TRACKING_QUERY_KEYS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_term",
        "utm_content",
        "utm_id",
        "fbclid",
        "gclid",
        "mc_cid",
        "mc_eid",
        "ref",
        "ref_src",
        "spm",
        "ncid",
    }
)

_MIN_TITLE_DEDUP_LEN = 16


def normalize_crawl_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""

    parsed = urlparse(raw)
    if not parsed.scheme or not parsed.netloc:
        return raw

    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]

    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")

    query_pairs = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=False)
        if key.lower() not in _TRACKING_QUERY_KEYS
    ]
    query = urlencode(sorted(query_pairs))

    return urlunparse((scheme, netloc, path, "", query, ""))


def normalize_title_for_dedup(title: str) -> str:
    normalized = normalize_keyword(title)
    normalized = re.sub(r"[^\w\s가-힣/+.&-]+", " ", normalized, flags=re.UNICODE)
    return re.sub(r"\s+", " ", normalized).strip()


def compute_content_hash(url: str | None, title: str) -> str:
    """Stable dedup key: canonical URL when available, otherwise normalized title."""
    norm_url = normalize_crawl_url(url or "")
    if norm_url:
        payload = norm_url
    else:
        payload = normalize_title_for_dedup(title)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def find_existing_post(
    db: Session,
    organization_id: UUID,
    url: str | None,
    title: str,
    *,
    include_deleted: bool = False,
) -> Post | None:
    excluded_statuses = [PostStatus.hidden]
    if not include_deleted:
        excluded_statuses.append(PostStatus.deleted)

    content_hash = compute_content_hash(url, title)
    existing = db.scalar(
        select(Post).where(
            Post.organization_id == organization_id,
            Post.content_hash == content_hash,
            Post.status.not_in(excluded_statuses),
        )
    )
    if existing:
        return existing

    raw_url = (url or "").strip()
    if raw_url:
        if get_settings().search_uses_elasticsearch:
            from app.search.post_content import find_post_id_by_url

            es_post_id = find_post_id_by_url(organization_id, raw_url)
            if es_post_id:
                es_post = db.get(Post, es_post_id)
                if es_post and es_post.status not in excluded_statuses:
                    return es_post

        existing = db.scalar(
            select(Post).where(
                Post.organization_id == organization_id,
                Post.original_url == raw_url,
                Post.status.not_in(excluded_statuses),
            )
        )
        if existing:
            return existing

        norm_url = normalize_crawl_url(raw_url)
        if norm_url and norm_url != raw_url:
            recent = db.scalars(
                select(Post)
                .where(
                    Post.organization_id == organization_id,
                    Post.original_url.is_not(None),
                    Post.status.not_in(excluded_statuses),
                )
                .order_by(Post.collected_at.desc())
                .limit(1500)
            ).all()
            for post in recent:
                if normalize_crawl_url(post.original_url or "") == norm_url:
                    return post

    norm_title = normalize_title_for_dedup(title)
    if len(norm_title) >= _MIN_TITLE_DEDUP_LEN:
        return db.scalar(
            select(Post)
            .where(
                Post.organization_id == organization_id,
                func.lower(Post.title) == norm_title,
                Post.status.not_in(excluded_statuses),
            )
            .order_by(Post.collected_at.desc())
            .limit(1)
        )

    return None
