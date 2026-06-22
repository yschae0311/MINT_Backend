"""Shared helpers for community / forum source types."""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.models.enums import SourceType

COMMUNITY_SOURCE_TYPES: frozenset[SourceType] = frozenset(
    {SourceType.reddit, SourceType.community_forum}
)
COMMUNITY_MIN_CONTENT_LEN = 130
REDDIT_REQUEST_DELAY_SEC = 2.0
REDDIT_USER_AGENT = "MINT/1.0 (EV intelligence desk; contact: admin@motrexev.local)"

_FORUM_LINK_PATTERNS = (
    re.compile(r"/service/board/", re.I),  # clien.net
    re.compile(r"/board/bbs_(?:list|view)", re.I),  # bobaedream
    re.compile(r"/community/[a-z0-9_-]+/\d", re.I),
    re.compile(r"/forum/", re.I),
    re.compile(r"/thread/", re.I),
    re.compile(r"/posts/\d", re.I),
)


def is_community_source_type(source_type: SourceType) -> bool:
    return source_type in COMMUNITY_SOURCE_TYPES


def reddit_listing_json_url(url: str, *, limit: int = 25) -> str:
    parsed = urlparse(url.strip())
    if "reddit.com" not in parsed.netloc.lower():
        return url
    path = parsed.path.rstrip("/")
    if path.endswith(".json"):
        return url
    if path.endswith(".rss"):
        path = path[: -len(".rss")]
    return f"https://{parsed.netloc}{path}.json?limit={limit}"


def reddit_post_url(permalink: str) -> str:
    if permalink.startswith("http"):
        return permalink
    return urljoin("https://www.reddit.com", permalink)


def extract_forum_article_links(
    soup: BeautifulSoup,
    base_url: str,
    *,
    skip_href: re.Pattern[str],
) -> list[tuple[str, str]]:
    """Forum-aware link extraction; falls back to generic article/main anchors."""
    base_host = urlparse(base_url).netloc
    seen: set[str] = set()
    results: list[tuple[str, str]] = []

    containers = soup.select("table.board_list, div.list_item, main, article, #content, .board-list")
    if not containers:
        containers = [soup.body] if soup.body else []

    for container in containers:
        if not container:
            continue
        for anchor in container.find_all("a", href=True):
            href = anchor["href"].strip()
            if not href or skip_href.search(href):
                continue
            full_url = urljoin(base_url, href)
            parsed = urlparse(full_url)
            if parsed.scheme not in ("http", "https"):
                continue
            if parsed.netloc and parsed.netloc != base_host:
                continue
            if not any(p.search(full_url) for p in _FORUM_LINK_PATTERNS):
                continue

            title = anchor.get_text(separator=" ", strip=True)
            if len(title) < 4 or len(title) > 200:
                continue
            if full_url in seen or full_url.rstrip("/") == base_url.rstrip("/"):
                continue

            seen.add(full_url)
            results.append((title, full_url))

    return results
