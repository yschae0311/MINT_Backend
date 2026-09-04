"""Pick a usable photo URL from crawled article HTML."""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

_SKIP_SRC = re.compile(
    r"sprite|1x1|pixel|tracking|doubleclick|gravatar|badge|icon[-_/]|logo[-_/]|emoji",
    re.I,
)
_SKIP_ATTR = re.compile(r"logo|icon|avatar|sprite|share|sns", re.I)


def extract_article_image_url(html: str, page_url: str) -> str | None:
    if not html or not page_url:
        return None
    soup = BeautifulSoup(html, "html.parser")
    for candidate in _meta_images(soup, page_url):
        if _usable_src(candidate):
            return candidate
    for candidate in _body_images(soup, page_url):
        if _usable_src(candidate):
            return candidate
    return None


def _meta_images(soup: BeautifulSoup, page_url: str) -> list[str]:
    keys = (
        ("property", "og:image"),
        ("property", "og:image:url"),
        ("name", "twitter:image"),
        ("name", "twitter:image:src"),
    )
    found: list[str] = []
    for attr, value in keys:
        tag = soup.find("meta", attrs={attr: value})
        if not isinstance(tag, Tag):
            continue
        raw = (tag.get("content") or "").strip()
        absolute = _absolute(raw, page_url)
        if absolute:
            found.append(absolute)
    return found


def _body_images(soup: BeautifulSoup, page_url: str) -> list[str]:
    container = soup.find("article") or soup.find("main") or soup.body
    if not container:
        return []
    found: list[str] = []
    for img in container.find_all("img"):
        if not isinstance(img, Tag):
            continue
        if _skip_node(img):
            continue
        raw = (img.get("src") or img.get("data-src") or img.get("data-original") or "").strip()
        absolute = _absolute(raw, page_url)
        if absolute:
            found.append(absolute)
    return found


def _skip_node(img: Tag) -> bool:
    width = _int_attr(img, "width")
    height = _int_attr(img, "height")
    if width is not None and width < 120:
        return True
    if height is not None and height < 80:
        return True
    blob = " ".join(
        str(img.get(name) or "")
        for name in ("class", "id", "alt", "src")
    )
    return bool(_SKIP_ATTR.search(blob))


def _usable_src(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    if _SKIP_SRC.search(url):
        return False
    if (parsed.path or "").lower().endswith((".svg", ".ico", ".gif")):
        return False
    return True


def _absolute(raw: str, page_url: str) -> str | None:
    value = (raw or "").strip()
    if not value or value.startswith("data:"):
        return None
    if value.startswith("//"):
        value = urljoin(page_url, value)
    else:
        value = urljoin(page_url, value)
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https"):
        return None
    return value


def _int_attr(tag: Tag, name: str) -> int | None:
    raw = tag.get(name)
    if raw is None:
        return None
    try:
        return int(re.sub(r"[^\d]", "", str(raw)) or 0) or None
    except ValueError:
        return None
