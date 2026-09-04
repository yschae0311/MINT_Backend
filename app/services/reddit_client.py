"""Reddit listing fetch — OAuth API, authenticated RSS, or old.reddit HTML fallback."""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import feedparser
import httpx

from app.core.config import Settings, get_settings
from app.services.article_text import html_to_article_text
from app.services.community_sources import (
    REDDIT_REQUEST_DELAY_SEC,
    REDDIT_USER_AGENT,
    _reddit_subreddit_path,
    parse_old_reddit_listing_html,
    reddit_listing_rss_urls,
    reddit_old_listing_page_urls,
    reddit_post_url,
    resolve_reddit_rss_credentials,
)

logger = logging.getLogger(__name__)

_REDDIT_SUB_RE = re.compile(r"/r/([^/]+)", re.I)


def parse_subreddit_name(url: str) -> str | None:
    match = _REDDIT_SUB_RE.search(_reddit_subreddit_path(url))
    return match.group(1) if match else None


class RedditClient:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    def has_oauth(self) -> bool:
        s = self.settings
        return bool(
            s.reddit_client_id.strip()
            and s.reddit_client_secret.strip()
            and s.reddit_username.strip()
            and s.reddit_password.strip()
        )

    def has_rss_auth(self) -> bool:
        return resolve_reddit_rss_credentials(
            auth_url=self.settings.reddit_rss_auth_url,
            rss_user=self.settings.reddit_rss_user,
            rss_feed=self.settings.reddit_rss_feed,
        ) is not None

    def _rss_credentials(self) -> tuple[str, str] | None:
        return resolve_reddit_rss_credentials(
            auth_url=self.settings.reddit_rss_auth_url,
            rss_user=self.settings.reddit_rss_user,
            rss_feed=self.settings.reddit_rss_feed,
        )

    def _http_client(self) -> httpx.Client:
        proxy = self.settings.reddit_http_proxy.strip() or None
        return httpx.Client(timeout=20.0, follow_redirects=True, proxy=proxy)

    def _request_headers(self, *, accept: str) -> dict[str, str]:
        return {
            "User-Agent": REDDIT_USER_AGENT,
            "Accept": accept,
            "Accept-Language": "en-US,en;q=0.9",
        }

    def fetch_listing(self, source_url: str, *, limit: int) -> list[dict]:
        """Return raw Reddit post dicts from RSS, HTML, or OAuth."""
        last_error: Exception | None = None

        if self.has_rss_auth():
            try:
                entries = self._fetch_rss_entries(source_url, limit=limit, authenticated=True)
                if entries:
                    return entries
            except Exception as exc:
                last_error = exc
                logger.warning("Reddit RSS failed for %s: %s", source_url, exc)

            try:
                entries = self._fetch_html_listing(source_url, limit=limit)
                if entries:
                    logger.info("Reddit HTML fallback succeeded for %s", source_url)
                    return entries
            except Exception as exc:
                last_error = exc
                logger.warning("Reddit HTML fallback failed for %s: %s", source_url, exc)

            if self.has_oauth():
                return self._fetch_oauth_listing(source_url, limit=limit)

        elif self.has_oauth():
            return self._fetch_oauth_listing(source_url, limit=limit)
        else:
            try:
                entries = self._fetch_rss_entries(source_url, limit=limit, authenticated=False)
                if entries:
                    return entries
            except Exception as exc:
                last_error = exc

            try:
                entries = self._fetch_html_listing(source_url, limit=limit)
                if entries:
                    return entries
            except Exception as exc:
                last_error = exc

        if last_error:
            raise last_error
        return []

    def _oauth_user_agent(self) -> str:
        return f"MINT/1.0 (EV intelligence desk; u/{self.settings.reddit_username.strip()})"

    def _access_token(self) -> str:
        if self._token and time.time() < self._token_expires_at:
            return self._token

        s = self.settings
        auth = httpx.BasicAuth(s.reddit_client_id.strip(), s.reddit_client_secret.strip())
        data = {
            "grant_type": "password",
            "username": s.reddit_username.strip(),
            "password": s.reddit_password,
        }
        headers = {"User-Agent": self._oauth_user_agent()}
        with httpx.Client(timeout=20.0) as client:
            resp = client.post(
                "https://www.reddit.com/api/v1/access_token",
                auth=auth,
                data=data,
                headers=headers,
            )
            resp.raise_for_status()
            payload = resp.json()

        self._token = payload["access_token"]
        self._token_expires_at = time.time() + int(payload.get("expires_in", 3600)) - 60
        return self._token

    def _fetch_oauth_listing(self, source_url: str, *, limit: int) -> list[dict]:
        subreddit = parse_subreddit_name(source_url)
        if not subreddit:
            raise ValueError(f"Not a subreddit URL: {source_url}")

        headers = {
            "Authorization": f"bearer {self._access_token()}",
            "User-Agent": self._oauth_user_agent(),
        }
        with httpx.Client(timeout=20.0) as client:
            resp = client.get(
                f"https://oauth.reddit.com/r/{subreddit}/hot",
                headers=headers,
                params={"limit": limit, "raw_json": 1},
            )
            if resp.status_code == 429:
                time.sleep(REDDIT_REQUEST_DELAY_SEC * 2)
                resp = client.get(
                    f"https://oauth.reddit.com/r/{subreddit}/hot",
                    headers=headers,
                    params={"limit": limit, "raw_json": 1},
                )
            resp.raise_for_status()
            payload = resp.json()

        children = (payload.get("data") or {}).get("children") or []
        return [child.get("data") or {} for child in children]

    def _fetch_rss_entries(self, source_url: str, *, limit: int, authenticated: bool) -> list[dict]:
        creds = self._rss_credentials() if authenticated else None
        rss_user, rss_feed = creds if creds else ("", "")
        rss_urls = reddit_listing_rss_urls(
            source_url,
            limit=limit,
            rss_user=rss_user or None,
            rss_feed=rss_feed or None,
        )
        headers = self._request_headers(
            accept="application/atom+xml,application/rss+xml,application/xml;q=0.9,*/*;q=0.8",
        )
        last_error: Exception | None = None

        with self._http_client() as client:
            for rss_url in rss_urls:
                try:
                    resp = client.get(rss_url, headers=headers)
                    if resp.status_code == 429:
                        time.sleep(REDDIT_REQUEST_DELAY_SEC * 3)
                        resp = client.get(rss_url, headers=headers)
                    resp.raise_for_status()
                    feed = feedparser.parse(resp.text)
                    if not feed.entries:
                        continue
                    return self._entries_from_rss(feed)
                except Exception as exc:
                    last_error = exc
                    logger.debug("Reddit RSS attempt failed %s: %s", rss_url, exc)

        if last_error:
            raise last_error
        return []

    def _fetch_html_listing(self, source_url: str, *, limit: int) -> list[dict]:
        headers = self._request_headers(accept="text/html,application/xhtml+xml,*/*;q=0.8")
        last_error: Exception | None = None

        with self._http_client() as client:
            for page_url in reddit_old_listing_page_urls(source_url):
                try:
                    resp = client.get(page_url, headers=headers)
                    if resp.status_code == 429:
                        time.sleep(REDDIT_REQUEST_DELAY_SEC * 3)
                        resp = client.get(page_url, headers=headers)
                    resp.raise_for_status()
                    posts = parse_old_reddit_listing_html(resp.text, limit=limit)
                    if posts:
                        return posts
                except Exception as exc:
                    last_error = exc
                    logger.debug("Reddit HTML attempt failed %s: %s", page_url, exc)

        if last_error:
            raise last_error
        return []

    @staticmethod
    def _entries_from_rss(feed) -> list[dict]:
        results: list[dict] = []
        for entry in feed.entries:
            title = (entry.get("title") or "").strip()
            link = (entry.get("link") or "").strip()
            summary = entry.get("summary") or entry.get("description") or ""
            published = None
            if entry.get("published_parsed"):
                published = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            results.append(
                {
                    "title": title,
                    "url": link,
                    "permalink": urlparse(link).path if link else "",
                    "selftext": summary,
                    "created_utc": published.timestamp() if published else None,
                    "_from_rss": True,
                }
            )
        return results


def reddit_post_from_raw(data: dict) -> tuple[str, str, str, datetime | None]:
    title = (data.get("title") or "").strip()
    permalink = data.get("permalink") or ""
    post_url = reddit_post_url(permalink) if permalink else (data.get("url") or "").strip()
    if data.get("_from_rss"):
        selftext = html_to_article_text(data.get("selftext") or "")
    else:
        selftext = (data.get("selftext") or "").strip()
    published = None
    created_utc = data.get("created_utc")
    if created_utc:
        published = datetime.fromtimestamp(float(created_utc), tz=timezone.utc)
    return title, post_url, selftext, published


def is_reddit_access_denied(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in (403, 429)
    msg = str(exc).lower()
    return "403" in msg or "429" in msg or "blocked" in msg or "too many requests" in msg


def reddit_fetch_hint(exc: Exception | None, client: RedditClient) -> str:
    if not client.has_rss_auth() and not client.has_oauth():
        return (
            "서버 .env에 REDDIT_RSS_AUTH_URL(또는 REDDIT_RSS_USER/FEED)이 없습니다. "
            "Celery worker .env 설정 후 worker 재시작 필요."
        )
    blocked = exc is not None and is_reddit_access_denied(exc)
    if blocked:
        return (
            "AWS/EC2 IP에서 Reddit RSS가 403으로 차단되었습니다. "
            "최신 코드는 old.reddit HTML 폴백을 시도합니다. "
            "그래도 실패하면 REDDIT_HTTP_PROXY(선택) 설정을 검토하세요."
        )[:320]
    if client.has_rss_auth():
        if exc:
            return f"Reddit 수집 실패: {exc}"[:320]
        return "Reddit 응답이 비었습니다. 토큰 만료 시 old.reddit.com/prefs/feeds 에서 재발급하세요."
    if exc:
        return f"OAuth 수집 실패: {exc}"[:320]
    return "Reddit feed returned no entries"
