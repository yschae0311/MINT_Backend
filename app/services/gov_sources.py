"""Government / public policy site helpers for list pages and article bodies."""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from app.services.article_text import soup_to_article_text

_GOV_HOST_MARKERS = (
    "mcee.go.kr",
    "me.go.kr",
    "molit.go.kr",
    "korea.kr",
    "ev.or.kr",
    "go.kr",
)


def is_gov_notice_host(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(marker in host for marker in _GOV_HOST_MARKERS)


def extract_gov_article_links(
    soup: BeautifulSoup,
    base_url: str,
    *,
    skip_href: re.Pattern[str],
) -> list[tuple[str, str]]:
    """Extract board/article links from Korean government list pages."""
    base_host = urlparse(base_url).netloc
    seen: set[str] = set()
    results: list[tuple[str, str]] = []

    selectors = (
        'a[href*="read.do"]',
        'a[href*="newsId="]',
        'a[href*="view.do"]',
        'a[href*="detail"]',
        'a[href*="selectBBSListDtl"]',
    )
    for selector in selectors:
        for anchor in soup.select(selector):
            href = (anchor.get("href") or "").strip()
            if not href or skip_href.search(href):
                continue
            full_url = urljoin(base_url, href)
            parsed = urlparse(full_url)
            if parsed.scheme not in ("http", "https"):
                continue
            if parsed.netloc and parsed.netloc != base_host:
                continue

            title = anchor.get_text(separator=" ", strip=True)
            if len(title) < 4 or len(title) > 200:
                continue
            if full_url in seen or full_url.rstrip("/") == base_url.rstrip("/"):
                continue

            seen.add(full_url)
            results.append((title, full_url))

    return results


def extract_gov_article_text(soup: BeautifulSoup, url: str) -> str | None:
    host = urlparse(str(url)).netloc.lower()

    if "mcee.go.kr" in host or "me.go.kr" in host:
        for selector in (
            ".board_view",
            ".board_view_con",
            "#contents .content",
            "#contents",
            ".view_cont",
        ):
            node = soup.select_one(selector)
            if node:
                text = soup_to_article_text(node)
                if len(text) >= 80:
                    return text

    if "korea.kr" in host:
        for selector in (
            ".article",
            "#article",
            ".view_cont",
            ".policy_news",
            "#content",
        ):
            node = soup.select_one(selector)
            if node:
                text = soup_to_article_text(node)
                if len(text) >= 80:
                    return text

    if "molit.go.kr" in host:
        for selector in (".board_view", ".view_cont", "#contents", ".content"):
            node = soup.select_one(selector)
            if node:
                text = soup_to_article_text(node)
                if len(text) >= 80:
                    return text

    return None
