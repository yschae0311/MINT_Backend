"""Turn crawled HTML into readable plain-text article bodies."""

from __future__ import annotations

import re
from typing import Any

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

_DROP_TAGS = frozenset(
    {
        "script",
        "style",
        "nav",
        "footer",
        "header",
        "aside",
        "form",
        "noscript",
        "iframe",
        "svg",
        "button",
        "input",
        "select",
        "textarea",
    }
)
_LINE_BREAK_TAGS = frozenset({"br"})
_PARAGRAPH_TAGS = frozenset(
    {
        "p",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "blockquote",
        "pre",
        "tr",
        "dt",
        "dd",
        "figcaption",
        "section",
        "article",
        "div",
        "table",
        "ul",
        "ol",
        "hr",
        "figure",
    }
)
_LINE_SPACE = re.compile(r"[ \t\r\f\v\u00a0]+")
_WEIRD_SPACES = re.compile(r"[\u200b\u200c\u200d\ufeff]")


def html_to_article_text(html: str, *, max_chars: int | None = None) -> str:
    raw = html or ""
    if "<" not in raw:
        return normalize_article_text(raw, max_chars=max_chars)
    soup = BeautifulSoup(raw, "html.parser")
    return soup_to_article_text(soup, max_chars=max_chars)


def soup_to_article_text(node: Any, *, max_chars: int | None = None) -> str:
    if node is None:
        return ""
    parts: list[str] = []
    _walk(node, parts)
    return normalize_article_text("".join(parts), max_chars=max_chars)


def normalize_article_text(text: str, *, max_chars: int | None = None) -> str:
    cleaned = _WEIRD_SPACES.sub("", (text or "").replace("\r\n", "\n").replace("\r", "\n"))
    chunks: list[str] = []
    current: list[str] = []
    for raw_line in cleaned.split("\n"):
        line = _LINE_SPACE.sub(" ", raw_line).strip()
        if line:
            current.append(line)
            continue
        if current:
            chunks.append("\n".join(current))
            current = []
    if current:
        chunks.append("\n".join(current))
    body = "\n\n".join(chunks).strip()
    if max_chars is not None and len(body) > max_chars:
        cut = body[:max_chars]
        para = cut.rsplit("\n\n", 1)[0]
        body = para if len(para) >= max_chars // 2 else cut.rstrip()
    return body


def _walk(node: Any, parts: list[str]) -> None:
    if isinstance(node, Comment):
        return
    if isinstance(node, NavigableString):
        parts.append(str(node))
        return
    if not isinstance(node, Tag):
        return
    name = (node.name or "").lower()
    if name in _DROP_TAGS:
        return
    if name in _LINE_BREAK_TAGS:
        parts.append("\n")
        return
    if name == "hr":
        parts.append("\n\n")
        return
    for child in node.children:
        _walk(child, parts)
    if name in _PARAGRAPH_TAGS:
        parts.append("\n\n")
