"""Column helpers — avoid PostgreSQL CREATE TYPE on restricted DB users."""

from sqlalchemy import Enum

from app.core.config import get_settings


def str_enum(enum_cls: type, name: str) -> Enum:
    """Store enums as VARCHAR on PostgreSQL (no native ENUM type / CREATE TYPE)."""
    settings = get_settings()
    if settings.database_url.startswith("sqlite"):
        return Enum(enum_cls, name=name)
    return Enum(enum_cls, name=name, native_enum=False)
