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


def _reddit_subreddit_path(url: str) -> str:
    parsed = urlparse(url.strip())
    path = parsed.path.rstrip("/")
    if path.endswith(".json"):
        path = path[: -len(".json")]
    if path.endswith(".rss"):
        path = path[: -len(".rss")]
    return path


def reddit_listing_json_url(url: str, *, limit: int = 25) -> str:
    parsed = urlparse(url.strip())
    if "reddit.com" not in parsed.netloc.lower():
        return url
    path = _reddit_subreddit_path(url)
    return f"https://{parsed.netloc}{path}.json?limit={limit}"


def reddit_listing_rss_url(
    url: str,
    *,
    limit: int = 25,
    rss_user: str | None = None,
    rss_feed: str | None = None,
) -> str:
    """Subreddit Atom/RSS feed — append user/feed tokens to bypass server IP blocks."""
    parsed = urlparse(url.strip())
    if "reddit.com" not in parsed.netloc.lower():
        return url
    path = _reddit_subreddit_path(url)
    query = f"limit={limit}"
    if rss_user and rss_feed:
        query += f"&user={rss_user}&feed={rss_feed}"
    return f"https://{parsed.netloc}{path}/.rss?{query}"


def reddit_post_url(permalink: str) -> str:
    if permalink.startswith("http"):
        return permalink
    return urljoin("https://www.reddit.com", permalink)


def reddit_old_post_url(post_url: str) -> str:
    """old.reddit.com serves post HTML without the new SPA shell."""
    parsed = urlparse(post_url.strip())
    host = parsed.netloc.lower()
    if "reddit.com" not in host:
        return post_url
    old_host = "old.reddit.com"
    return parsed._replace(netloc=old_host).geturl()


def parse_reddit_rss_auth(url: str) -> tuple[str, str] | None:
    """Extract user/feed tokens from a private Reddit RSS URL (e.g. saved.rss?feed=…&user=…)."""
    from urllib.parse import parse_qs

    parsed = urlparse(url.strip())
    if "reddit.com" not in parsed.netloc.lower():
        return None
    query = parse_qs(parsed.query)
    user_vals = query.get("user") or []
    feed_vals = query.get("feed") or []
    user = (user_vals[0] if user_vals else "").strip()
    feed = (feed_vals[0] if feed_vals else "").strip()
    if user and feed:
        return user, feed
    return None


def resolve_reddit_rss_credentials(
    *, auth_url: str = "", rss_user: str = "", rss_feed: str = ""
) -> tuple[str, str] | None:
    parsed = parse_reddit_rss_auth(auth_url)
    if parsed:
        return parsed
    user = rss_user.strip()
    feed = rss_feed.strip()
    if user and feed:
        return user, feed
    return None


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
