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


def init_db() -> None:
    """Create tables that do not exist yet (dev-friendly; production should use Alembic)."""
    import app.models  # noqa: F401 — register all models on Base.metadata

    _ensure_pg_schema()
    inspector = inspect(engine)
    existing = set(
        inspector.get_table_names(schema=_schema) if _schema else inspector.get_table_names()
    )
    Base.metadata.create_all(bind=engine)
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
