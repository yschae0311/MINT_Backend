"""account approval status and inquiries

Revision ID: 006
Revises: 005
Create Date: 2026-06-23

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "approval_status",
            sa.String(16),
            nullable=False,
            server_default="approved",
        ),
    )

    op.create_table(
        "inquiries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="open"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_inquiries_organization_id", "inquiries", ["organization_id"])
    op.create_index("ix_inquiries_user_id", "inquiries", ["user_id"])

    op.create_table(
        "inquiry_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "inquiry_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("inquiries.id"),
            nullable=False,
        ),
        sa.Column(
            "author_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_inquiry_messages_inquiry_id", "inquiry_messages", ["inquiry_id"])


def downgrade() -> None:
    op.drop_index("ix_inquiry_messages_inquiry_id", table_name="inquiry_messages")
    op.drop_table("inquiry_messages")
    op.drop_index("ix_inquiries_user_id", table_name="inquiries")
    op.drop_index("ix_inquiries_organization_id", table_name="inquiries")
    op.drop_table("inquiries")
    op.drop_column("users", "approval_status")
