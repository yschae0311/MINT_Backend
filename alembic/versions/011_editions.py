"""editions, source_editions, keyword/category/report edition_id

Revision ID: 011
Revises: 010
Create Date: 2026-08-21
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "011"
down_revision: Union[str, None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "editions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0"),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("topic_terms", postgresql.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("organization_id", "slug"),
    )
    op.create_index("ix_editions_organization_id", "editions", ["organization_id"])
    op.create_table(
        "source_editions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "source_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sources.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "edition_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("editions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("source_id", "edition_id"),
    )
    op.create_index("ix_source_editions_source_id", "source_editions", ["source_id"])
    op.create_index("ix_source_editions_edition_id", "source_editions", ["edition_id"])
    op.add_column("news_categories", sa.Column("edition_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("keywords", sa.Column("is_featured", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.add_column("keywords", sa.Column("edition_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("daily_reports", sa.Column("edition_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_index("ix_news_categories_edition_id", "news_categories", ["edition_id"])
    op.create_index("ix_keywords_edition_id", "keywords", ["edition_id"])
    op.create_index("ix_daily_reports_edition_id", "daily_reports", ["edition_id"])


def downgrade() -> None:
    op.drop_index("ix_daily_reports_edition_id", table_name="daily_reports")
    op.drop_index("ix_keywords_edition_id", table_name="keywords")
    op.drop_index("ix_news_categories_edition_id", table_name="news_categories")
    op.drop_column("daily_reports", "edition_id")
    op.drop_column("keywords", "edition_id")
    op.drop_column("keywords", "is_featured")
    op.drop_column("news_categories", "edition_id")
    op.drop_index("ix_source_editions_edition_id", table_name="source_editions")
    op.drop_index("ix_source_editions_source_id", table_name="source_editions")
    op.drop_table("source_editions")
    op.drop_index("ix_editions_organization_id", table_name="editions")
    op.drop_table("editions")
