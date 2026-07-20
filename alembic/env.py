from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool, text

from app.core.config import get_settings
from app.core.database import Base
from app.models import (  # noqa: F401
    AIOutput,
    DailyReport,
    DailyReportItem,
    NotificationLog,
    Keyword,
    NewsCategory,
    Organization,
    PersonalReport,
    PersonalReportItem,
    PersonalReportView,
    Post,
    PostKeyword,
    RefreshToken,
    ReviewQueueItem,
    SlackWebhook,
    Source,
    User,
    UserCategorySubscription,
    UserKeywordSubscription,
)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata


def _pg_schema() -> str | None:
    return settings.effective_schema


def _prepare_connection(connection) -> str | None:
    schema = _pg_schema()
    if not schema:
        return None
    connection.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
    connection.execute(text(f'SET search_path TO "{schema}"'))
    return schema


def _configure(**extra):
    schema = _pg_schema()
    opts = {
        "target_metadata": target_metadata,
        "compare_type": True,
        **extra,
    }
    if schema:
        opts["version_table_schema"] = schema
        opts["include_schemas"] = True
    return opts


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(**_configure(url=url, literal_binds=True))
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        _prepare_connection(connection)
        context.configure(connection=connection, **_configure())
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
