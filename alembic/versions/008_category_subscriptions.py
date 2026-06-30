"""category subscriptions and curated keywords

Revision ID: 008
Revises: 007
Create Date: 2026-06-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "008"
down_revision: Union[str, None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.add_column(
        "keywords",
        sa.Column("is_curated", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "user_category_subscriptions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("category_id", UUID, sa.ForeignKey("news_categories.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("user_id", "category_id"),
    )
    op.create_index(
        "ix_user_category_subscriptions_user_id",
        "user_category_subscriptions",
        ["user_id"],
    )
    op.create_index(
        "ix_user_category_subscriptions_category_id",
        "user_category_subscriptions",
        ["category_id"],
    )


def downgrade() -> None:
    op.drop_table("user_category_subscriptions")
    op.drop_column("keywords", "is_curated")
