"""user_editions membership + users.keycloak_sub

Revision ID: 012
Revises: 011
Create Date: 2026-08-21
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "012"
down_revision: Union[str, None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("keycloak_sub", sa.String(length=128), nullable=True))
    op.create_index("ix_users_keycloak_sub", "users", ["keycloak_sub"], unique=True)
    op.alter_column(
        "users",
        "password_hash",
        existing_type=sa.String(length=255),
        nullable=True,
    )
    op.create_table(
        "user_editions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("edition_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("is_editor", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["edition_id"], ["editions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "edition_id"),
    )
    op.create_index("ix_user_editions_user_id", "user_editions", ["user_id"])
    op.create_index("ix_user_editions_edition_id", "user_editions", ["edition_id"])
    op.create_index(
        "uq_user_editions_one_editor",
        "user_editions",
        ["edition_id"],
        unique=True,
        postgresql_where=sa.text("is_editor IS TRUE"),
        sqlite_where=sa.text("is_editor = 1"),
    )


def downgrade() -> None:
    op.drop_index("uq_user_editions_one_editor", table_name="user_editions")
    op.drop_index("ix_user_editions_edition_id", table_name="user_editions")
    op.drop_index("ix_user_editions_user_id", table_name="user_editions")
    op.drop_table("user_editions")
    op.drop_index("ix_users_keycloak_sub", table_name="users")
    op.drop_column("users", "keycloak_sub")
    op.alter_column(
        "users",
        "password_hash",
        existing_type=sa.String(length=255),
        nullable=False,
    )
