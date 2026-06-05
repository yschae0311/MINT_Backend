from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "MINT"
    app_env: str = "development"
    debug: bool = True

    database_url: str = "sqlite:///./mint_dev.db"
    """PostgreSQL on managed hosts: use a dedicated schema (public may deny CREATE)."""
    db_schema: str = "mint"
    redis_url: str = "redis://localhost:6379/0"

    jwt_secret_key: str = "change-me"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 1440

    llm_provider: str = "gemini"
    gemini_api_key: str = ""
    gemini_summary_model: str = "gemini-2.5-flash-lite"
    gemini_report_model: str = "gemini-2.5-flash"

    slack_webhook_encryption_key: str = "change-me-32-byte-key-here!!!!"

    cors_origins: str = "http://localhost:5173"

    seed_admin_email: str = "admin@motrexev.com"
    seed_admin_password: str = "admin1234"
    seed_admin_name: str = "김민트"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    @property
    def effective_schema(self) -> str | None:
        return None if self.is_sqlite else self.db_schema


@lru_cache
def get_settings() -> Settings:
    return Settings()
