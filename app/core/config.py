from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = Path(__file__).resolve().parents[2]
_ENV_FILE = _BACKEND_DIR / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_FILE if _ENV_FILE.is_file() else ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "MINT"
    app_env: str = "development"
    debug: bool = True

    database_url: str = "sqlite:///./mint_dev.db"
    """PostgreSQL on managed hosts: use a dedicated schema (public may deny CREATE)."""
    db_schema: str = "mint"
    redis_url: str = "redis://localhost:6379/0"

    jwt_secret_key: str = "change-me"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60
    jwt_refresh_token_expire_days: int = 30

    llm_provider: str = "gemini"
    gemini_api_key: str = ""
    gemini_summary_model: str = "gemini-2.5-flash-lite"
    gemini_report_model: str = "gemini-2.5-flash"
    gemini_image_model: str = "gemini-2.5-flash-image"
    report_illustration_enabled: bool = True
    personalization_enabled: bool = False
    classification_confidence_threshold: float = 0.6

    # Gemini TTS (server narration). Browser SpeechSynthesis is the frontend fallback.
    tts_enabled: bool = True
    gemini_tts_model: str = "gemini-2.5-flash-preview-tts"
    gemini_tts_voice: str = "Kore"

    translate_titles_on_crawl: bool = True

    media_root: str = "uploads"
    media_url_prefix: str = "/media"

    # Daily corner weather (Open-Meteo, no API key). Default: Seoul.
    weather_enabled: bool = True
    weather_latitude: float = 37.5665
    weather_longitude: float = 126.9780
    weather_location_name: str = "서울"

    slack_webhook_encryption_key: str = "change-me-32-byte-key-here!!!!"

    cors_origins: str = "http://localhost:5173"
    """Public MINT web URL for Slack links etc. Falls back to first CORS origin if unset."""
    frontend_url: str = ""

    seed_admin_email: str = "admin@motrexev.com"
    seed_admin_password: str = "admin1234"
    seed_admin_name: str = "김민트"

    """AI 발견 게시판에서 검토 대기(pending) 상태로 남은 글을 soft-delete 하는 보관 일수. 0이면 비활성."""
    discovery_pending_retention_days: int = 14
    """1면·뉴스 기본 표시 및 published 글 정리 일수. 0이면 표시 창/정리를 쓰지 않음."""
    feed_window_days: int = 7
    post_retention_days: int = 7

    # Reddit — 서버 IP에서 비인증 JSON/RSS는 403/429. OAuth 또는 RSS 토큰 중 하나 필요.
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_username: str = ""
    reddit_password: str = ""
    """https://old.reddit.com/prefs/feeds 의 saved 등 private 피드 URL 전체를 붙여넣어도 됨"""
    reddit_rss_auth_url: str = ""
    reddit_rss_user: str = ""
    reddit_rss_feed: str = ""
    """EC2 등 datacenter IP에서 Reddit RSS가 403이면 HTTP 프록시(선택)"""
    reddit_http_proxy: str = ""

    # Elasticsearch — 전문 검색·챗봇 RAG (미설정 시 PostgreSQL ILIKE 유지)
    search_backend: str = "postgres"
    """postgres | elasticsearch | dual"""
    elasticsearch_url: str = ""
    elasticsearch_username: str = ""
    elasticsearch_password: str = ""
    elasticsearch_ca_certs: str = ""
    """CA bundle path (.crt/.pem). Relative paths resolve from MINT_Backend root."""
    elasticsearch_verify_certs: bool = True
    elasticsearch_index_posts: str = "mint-posts-v1"
    elasticsearch_index_posts_alias: str = "mint-posts-current"
    elasticsearch_request_timeout_sec: float = 10.0
    search_dual_log_diff: bool = False
    elasticsearch_text_analyzer: str = "nori"
    """auto: detect nori plugin | nori | standard"""

    # Keycloak SSO — public client + PKCE. No client secret.
    keycloak_issuer: str = ""
    keycloak_client_id: str = ""
    keycloak_admin_role: str = "mint-superadmin"

    @property
    def keycloak_configured(self) -> bool:
        return bool(self.keycloak_issuer.strip() and self.keycloak_client_id.strip())

    @property
    def search_uses_elasticsearch(self) -> bool:
        return bool(self.elasticsearch_url.strip()) and self.search_backend in (
            "elasticsearch",
            "dual",
        )

    @property
    def search_uses_postgres(self) -> bool:
        if not self.elasticsearch_url.strip():
            return True
        return self.search_backend in ("postgres", "dual")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def public_frontend_url(self) -> str | None:
        """Slack·알림용 공개 프론트 URL. localhost는 운영 알림에 쓰지 않음."""
        candidates: list[str] = []
        if self.frontend_url.strip():
            candidates.append(self.frontend_url.strip())
        candidates.extend(self.cors_origin_list)

        for raw in candidates:
            url = raw.rstrip("/")
            if not url:
                continue
            if self._is_local_url(url) and self.app_env.lower() == "production":
                continue
            return url
        return None

    @staticmethod
    def _is_local_url(url: str) -> bool:
        lowered = url.lower()
        return any(
            host in lowered
            for host in ("localhost", "127.0.0.1", "0.0.0.0", "[::1]")
        )

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def effective_schema(self) -> str | None:
        return None if self.is_sqlite else self.db_schema


@lru_cache
def get_settings() -> Settings:
    return Settings()
