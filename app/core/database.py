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
