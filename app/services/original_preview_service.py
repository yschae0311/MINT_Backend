import logging
import re
from urllib.parse import urlparse
from uuid import UUID

import httpx
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestError, NotFoundError
from app.services.crawler_service import _DEFAULT_HEADERS
from app.services.post_service import PostService

logger = logging.getLogger(__name__)

_STRIP_TAGS = ("script", "iframe", "object", "embed", "form", "noscript")
_PREVIEW_CSS = """
<style data-mint-preview>
  html, body { margin: 0; padding: 12px 16px; font-family: system-ui, sans-serif; line-height: 1.6; }
  img, video { max-width: 100%; height: auto; }
  a { color: #0d6b52; }
</style>
"""


class OriginalPreviewService:
    def __init__(self, db: Session):
        self.db = db
        self.timeout = 15.0

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
