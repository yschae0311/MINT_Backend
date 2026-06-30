import logging
import re
from collections.abc import Mapping
from urllib.parse import urlparse
from uuid import UUID

import httpx
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestError, NotFoundError
from app.services.crawler_service import _DEFAULT_HEADERS
from app.services.post_service import PostService

logger = logging.getLogger(__name__)

_FRAME_ANCESTORS_RE = re.compile(r"frame-ancestors\s+([^;]+)", re.I)
_STRIP_TAGS = ("script", "iframe", "object", "embed", "form", "noscript")
_PREVIEW_CSS = """
<style data-mint-preview>
  html, body { margin: 0; padding: 12px 16px; font-family: system-ui, sans-serif; line-height: 1.6; }
  img, video { max-width: 100%; height: auto; }
  a { color: #0d6b52; }
</style>
"""


def _iter_header_pairs(headers: Mapping[str, str]):
    multi_items = getattr(headers, "multi_items", None)
    if callable(multi_items):
        yield from multi_items()
        return
    for key, value in headers.items():
        yield key, value


_XFO_BLOCK_VALUES = frozenset({"DENY", "SAMEORIGIN"})


def _x_frame_options_value_blocks(value: str) -> bool:
    normalized = value.strip().upper()
    if normalized in _XFO_BLOCK_VALUES:
        return True
    return any(part.strip().upper() in _XFO_BLOCK_VALUES for part in normalized.split(","))


def _csp_blocks_frame_embed(csp: str) -> bool:
    match = _FRAME_ANCESTORS_RE.search(csp)
    if not match:
        return False

    tokens = [t.strip().lower() for t in match.group(1).split() if t.strip()]
    if not tokens:
        return False
    if "'none'" in tokens or "none" in tokens:
        return True
    if tokens == ["'self'"] or tokens == ["self"]:
        return True
    if "*" in tokens:
        return False
    return True


def iframe_embed_blocked(headers: Mapping[str, str], html_head: str = "") -> bool:
    """Return True when the page likely blocks cross-origin iframe embed."""
    for key, value in _iter_header_pairs(headers):
        lowered_key = key.lower()
        if lowered_key == "x-frame-options" and _x_frame_options_value_blocks(value):
            return True
        if lowered_key in ("content-security-policy", "content-security-policy-report-only"):
            if value and _csp_blocks_frame_embed(value):
                return True

    if not html_head:
        return False

    soup = BeautifulSoup(html_head, "html.parser")
    for meta in soup.find_all("meta"):
        http_equiv = (meta.get("http-equiv") or "").strip().lower()
        content = (meta.get("content") or "").strip()
        if http_equiv == "x-frame-options" and _x_frame_options_value_blocks(content):
            return True
        if http_equiv == "content-security-policy" and _csp_blocks_frame_embed(content):
            return True

    return False


class OriginalPreviewService:
    def __init__(self, db: Session):
        self.db = db
        self.timeout = 15.0

    def can_embed_in_iframe(self, post_id: UUID, organization_id: UUID) -> bool:
        post = PostService(self.db).get_post(post_id, organization_id)
        url = (post.original_url or "").strip()
        if not url:
            return False

        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False

        try:
            with httpx.Client(
                timeout=self.timeout,
                follow_redirects=True,
                headers=_DEFAULT_HEADERS,
            ) as client:
                resp = client.get(url)
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("embed check fetch failed url=%s err=%s", url, exc)
            return False

        html_head = ""
        if "html" in resp.headers.get("content-type", "").lower() or "<html" in resp.text[:500].lower():
            html_head = resp.text[:8192]

        return not iframe_embed_blocked(resp.headers, html_head)

    def build_preview(self, post_id: UUID, organization_id: UUID) -> str:
        post = PostService(self.db).get_post(post_id, organization_id)
        url = (post.original_url or "").strip()
        if not url:
            raise BadRequestError("원문 URL이 없습니다.")

        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            raise BadRequestError("지원하지 않는 URL 형식입니다.")

        try:
            with httpx.Client(
                timeout=self.timeout,
                follow_redirects=True,
                headers=_DEFAULT_HEADERS,
            ) as client:
                resp = client.get(url)
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("original preview fetch failed url=%s err=%s", url, exc)
            raise BadRequestError("원문 페이지를 불러오지 못했습니다.") from exc

        content_type = resp.headers.get("content-type", "")
        if "html" not in content_type.lower() and "<html" not in resp.text[:500].lower():
            raise BadRequestError("HTML 원문만 미리보기할 수 있습니다.")

        return self._prepare_html(resp.text, url)

    def _prepare_html(self, raw_html: str, base_url: str) -> str:
        soup = BeautifulSoup(raw_html, "html.parser")

        for tag_name in _STRIP_TAGS:
            for tag in soup.find_all(tag_name):
                tag.decompose()

        if not soup.head:
            head = soup.new_tag("head")
            if soup.html:
                soup.html.insert(0, head)
            else:
                html = soup.new_tag("html")
                html.append(head)
                if soup.body:
                    html.append(soup.body.extract())
                soup.append(html)

        existing_base = soup.head.find("base")
        if existing_base:
            existing_base["href"] = base_url
        else:
            base_tag = soup.new_tag("base", href=base_url)
            soup.head.insert(0, base_tag)

        if not soup.head.find("meta", attrs={"charset": True}):
            charset = soup.new_tag("meta", charset="utf-8")
            soup.head.insert(0, charset)

        soup.head.append(BeautifulSoup(_PREVIEW_CSS, "html.parser"))

        out = str(soup)
        if not re.search(r"<html[\s>]", out, re.I):
            out = f"<!DOCTYPE html><html><head><base href=\"{base_url}\">{_PREVIEW_CSS}</head><body>{out}</body></html>"
        return out
