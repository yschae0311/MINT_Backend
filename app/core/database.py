import logging
import re
from collections.abc import Generator

from sqlalchemy import MetaData, create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_schema = settings.effective_schema
_metadata = MetaData(schema=_schema) if _schema else MetaData()

_connect_args = {"check_same_thread": False} if settings.is_sqlite else {}
engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args=_connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    metadata = _metadata


def _ensure_pg_schema() -> None:
    if not _schema:
        return
    if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", _schema):
        raise ValueError(f"Invalid db_schema: {_schema!r}")
    with engine.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{_schema}"'))
    logger.info('Ensured PostgreSQL schema "%s"', _schema)


def _migrate_pg_columns() -> None:
    """Lightweight dev migrations for columns created before enum values grew."""
    if settings.is_sqlite or not _schema:
        return
    with engine.begin() as conn:
        conn.execute(
            text(
                f'ALTER TABLE "{_schema}".background_jobs '
                "ALTER COLUMN status TYPE VARCHAR(16)"
            )
        )
        conn.execute(
            text(
                f'ALTER TABLE "{_schema}".background_jobs '
                "ALTER COLUMN job_type TYPE VARCHAR(32)"
            )
        )
        conn.execute(
            text(
                f'ALTER TABLE "{_schema}".sources '
                "ALTER COLUMN source_type TYPE VARCHAR(32)"
            )
        )
        conn.execute(
            text(
                f'ALTER TABLE "{_schema}".organizations '
                "ADD COLUMN IF NOT EXISTS discovery_pending_retention_days INTEGER"
            )
        )
        conn.execute(
            text(
                f'ALTER TABLE "{_schema}".daily_reports '
                "ADD COLUMN IF NOT EXISTS illustration_url VARCHAR(512)"
            )
        )
        conn.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS "{_schema}".editions (
                    id UUID PRIMARY KEY,
                    organization_id UUID NOT NULL REFERENCES "{_schema}".organizations(id),
                    slug VARCHAR(64) NOT NULL,
                    name VARCHAR(128) NOT NULL,
                    sort_order INTEGER DEFAULT 0,
                    is_active BOOLEAN DEFAULT true,
                    topic_terms JSONB,
                    created_at TIMESTAMPTZ DEFAULT now(),
                    updated_at TIMESTAMPTZ DEFAULT now(),
                    UNIQUE (organization_id, slug)
                )
                """
            )
        )
        conn.execute(
            text(
                f'CREATE INDEX IF NOT EXISTS ix_editions_organization_id '
                f'ON "{_schema}".editions (organization_id)'
            )
        )
        conn.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS "{_schema}".source_editions (
                    id UUID PRIMARY KEY,
                    source_id UUID NOT NULL REFERENCES "{_schema}".sources(id) ON DELETE CASCADE,
                    edition_id UUID NOT NULL REFERENCES "{_schema}".editions(id) ON DELETE CASCADE,
                    created_at TIMESTAMPTZ DEFAULT now(),
                    UNIQUE (source_id, edition_id)
                )
                """
            )
        )
        conn.execute(
            text(
                f'CREATE INDEX IF NOT EXISTS ix_source_editions_source_id '
                f'ON "{_schema}".source_editions (source_id)'
            )
        )
        conn.execute(
            text(
                f'CREATE INDEX IF NOT EXISTS ix_source_editions_edition_id '
                f'ON "{_schema}".source_editions (edition_id)'
            )
        )
        conn.execute(
            text(
                f'ALTER TABLE "{_schema}".users '
                "ADD COLUMN IF NOT EXISTS approval_status VARCHAR(16) DEFAULT 'approved'"
            )
        )
        conn.execute(
            text(
                f'ALTER TABLE "{_schema}".keywords '
                "ADD COLUMN IF NOT EXISTS is_curated BOOLEAN NOT NULL DEFAULT false"
            )
        )
        conn.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS "{_schema}".user_category_subscriptions (
                    id UUID PRIMARY KEY,
                    user_id UUID NOT NULL REFERENCES "{_schema}".users(id),
                    category_id UUID NOT NULL REFERENCES "{_schema}".news_categories(id),
                    created_at TIMESTAMPTZ DEFAULT now(),
                    UNIQUE (user_id, category_id)
                )
                """
            )
        )
        conn.execute(
            text(
                f'CREATE INDEX IF NOT EXISTS ix_user_category_subscriptions_user_id '
                f'ON "{_schema}".user_category_subscriptions (user_id)'
            )
        )
        conn.execute(
            text(
                f'CREATE INDEX IF NOT EXISTS ix_user_category_subscriptions_category_id '
                f'ON "{_schema}".user_category_subscriptions (category_id)'
            )
        )
        conn.execute(
            text(
                f'ALTER TABLE "{_schema}".news_categories '
                "ADD COLUMN IF NOT EXISTS is_featured BOOLEAN NOT NULL DEFAULT true"
            )
        )
        conn.execute(
            text(
                f'ALTER TABLE "{_schema}".news_categories '
                "ADD COLUMN IF NOT EXISTS edition_id UUID"
            )
        )
        conn.execute(
            text(
                f'ALTER TABLE "{_schema}".keywords '
                "ADD COLUMN IF NOT EXISTS is_featured BOOLEAN NOT NULL DEFAULT false"
            )
        )
        conn.execute(
            text(
                f'ALTER TABLE "{_schema}".keywords '
                "ADD COLUMN IF NOT EXISTS edition_id UUID"
            )
        )
        conn.execute(
            text(
                f'ALTER TABLE "{_schema}".daily_reports '
                "ADD COLUMN IF NOT EXISTS edition_id UUID"
            )
        )
        conn.execute(
            text(
                f'CREATE INDEX IF NOT EXISTS ix_news_categories_edition_id '
                f'ON "{_schema}".news_categories (edition_id)'
            )
        )
        conn.execute(
            text(
                f'CREATE INDEX IF NOT EXISTS ix_keywords_edition_id '
                f'ON "{_schema}".keywords (edition_id)'
            )
        )
        conn.execute(
            text(
                f'CREATE INDEX IF NOT EXISTS ix_daily_reports_edition_id '
                f'ON "{_schema}".daily_reports (edition_id)'
            )
        )
        conn.execute(
            text(
                f'ALTER TABLE "{_schema}".users '
                "ADD COLUMN IF NOT EXISTS keycloak_sub VARCHAR(128)"
            )
        )
        conn.execute(
            text(
                f'CREATE UNIQUE INDEX IF NOT EXISTS ix_users_keycloak_sub '
                f'ON "{_schema}".users (keycloak_sub)'
            )
        )
        conn.execute(
            text(
                f'ALTER TABLE "{_schema}".users '
                "ALTER COLUMN password_hash DROP NOT NULL"
            )
        )
        conn.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS "{_schema}".user_editions (
                    id UUID PRIMARY KEY,
                    user_id UUID NOT NULL REFERENCES "{_schema}".users(id) ON DELETE CASCADE,
                    edition_id UUID NOT NULL REFERENCES "{_schema}".editions(id) ON DELETE CASCADE,
                    is_editor BOOLEAN NOT NULL DEFAULT false,
                    created_at TIMESTAMPTZ DEFAULT now(),
                    UNIQUE (user_id, edition_id)
                )
                """
            )
        )
        conn.execute(
            text(
                f'CREATE INDEX IF NOT EXISTS ix_user_editions_user_id '
                f'ON "{_schema}".user_editions (user_id)'
            )
        )
        conn.execute(
            text(
                f'CREATE INDEX IF NOT EXISTS ix_user_editions_edition_id '
                f'ON "{_schema}".user_editions (edition_id)'
            )
        )
        conn.execute(
            text(f'DROP INDEX IF EXISTS "{_schema}".uq_user_editions_one_editor')
        )
    logger.debug('Ensured background_jobs.status column width')


def _migrate_sqlite_columns() -> None:
    if not settings.is_sqlite:
        return
    inspector = inspect(engine)
    if "daily_reports" not in inspector.get_table_names():
        return
    cols = {c["name"] for c in inspector.get_columns("daily_reports")}
    if "illustration_url" not in cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE daily_reports ADD COLUMN illustration_url VARCHAR(512)"))
        logger.info("Added daily_reports.illustration_url column (sqlite)")
    if "users" in inspector.get_table_names():
        user_cols = {c["name"] for c in inspector.get_columns("users")}
        if "approval_status" not in user_cols:
            with engine.begin() as conn:
                conn.execute(
                    text(
                        "ALTER TABLE users ADD COLUMN approval_status VARCHAR(16) "
                        "NOT NULL DEFAULT 'approved'"
                    )
                )
            logger.info("Added users.approval_status column (sqlite)")

    def _add_sqlite_col(table: str, column: str, ddl: str) -> None:
        if table not in inspector.get_table_names():
            return
        cols = {c["name"] for c in inspector.get_columns(table)}
        if column in cols:
            return
        with engine.begin() as conn:
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))
        logger.info("Added %s.%s column (sqlite)", table, column)

    _add_sqlite_col("news_categories", "edition_id", "edition_id CHAR(32)")
    _add_sqlite_col("keywords", "is_featured", "is_featured BOOLEAN NOT NULL DEFAULT 0")
    _add_sqlite_col("keywords", "edition_id", "edition_id CHAR(32)")
    _add_sqlite_col("daily_reports", "edition_id", "edition_id CHAR(32)")
    _add_sqlite_col("users", "keycloak_sub", "keycloak_sub VARCHAR(128)")


def init_db() -> None:
    """Create tables that do not exist yet (dev-friendly; production should use Alembic)."""
    import app.models  # noqa: F401 — register all models on Base.metadata

    _ensure_pg_schema()
    inspector = inspect(engine)
    existing = set(
        inspector.get_table_names(schema=_schema) if _schema else inspector.get_table_names()
    )
    Base.metadata.create_all(bind=engine)
    if existing:
        try:
            _migrate_pg_columns()
        except Exception as exc:
            logger.debug("PostgreSQL column migration skipped: %s", exc)
        try:
            _migrate_sqlite_columns()
        except Exception as exc:
            logger.debug("SQLite column migration skipped: %s", exc)
    created = [t.name for t in Base.metadata.sorted_tables if t.name not in existing]
    if created:
        loc = f'{_schema}.' if _schema else ""
        logger.info("Created tables: %s", ", ".join(f"{loc}{n}" for n in created))
    else:
        logger.debug("Database schema already up to date (%d tables)", len(existing))


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
