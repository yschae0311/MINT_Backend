"""keyword personalization and personal reports

Revision ID: 007
Revises: 006
Create Date: 2026-06-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "news_categories",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("organization_id", UUID, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("normalized_name", sa.String(128), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("organization_id", "normalized_name"),
    )
    op.create_index("ix_news_categories_organization_id", "news_categories", ["organization_id"])

    op.create_table(
        "keywords",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("organization_id", UUID, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("category_id", UUID, sa.ForeignKey("news_categories.id"), nullable=True),
        sa.Column("owner_user_id", UUID, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("normalized_name", sa.String(128), nullable=False),
        sa.Column("aliases", postgresql.JSON(), nullable=True),
        sa.Column("scope", sa.String(16), nullable=False, server_default="organization"),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("usage_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("organization_id", "owner_user_id", "normalized_name"),
    )
    for column in ("organization_id", "category_id", "owner_user_id", "normalized_name"):
        op.create_index(f"ix_keywords_{column}", "keywords", [column])

    op.create_table(
        "user_keyword_subscriptions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("keyword_id", UUID, sa.ForeignKey("keywords.id"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("user_id", "keyword_id"),
    )
    op.create_index("ix_user_keyword_subscriptions_user_id", "user_keyword_subscriptions", ["user_id"])
    op.create_index("ix_user_keyword_subscriptions_keyword_id", "user_keyword_subscriptions", ["keyword_id"])

    op.create_table(
        "post_keywords",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("post_id", UUID, sa.ForeignKey("posts.id"), nullable=False),
        sa.Column("keyword_id", UUID, sa.ForeignKey("keywords.id"), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="1"),
        sa.Column("matched_by", sa.String(16), nullable=False, server_default="ai"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("post_id", "keyword_id"),
    )
    op.create_index("ix_post_keywords_post_id", "post_keywords", ["post_id"])
    op.create_index("ix_post_keywords_keyword_id", "post_keywords", ["keyword_id"])

    op.create_table(
        "personal_reports",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("organization_id", UUID, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("model", sa.String(128), nullable=False, server_default=""),
        sa.Column("prompt_version", sa.String(32), nullable=False, server_default="personal_v1"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("user_id", "report_date"),
    )
    op.create_index("ix_personal_reports_organization_id", "personal_reports", ["organization_id"])
    op.create_index("ix_personal_reports_user_id", "personal_reports", ["user_id"])
    op.create_index("ix_personal_reports_report_date", "personal_reports", ["report_date"])

    op.create_table(
        "personal_report_items",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("report_id", UUID, sa.ForeignKey("personal_reports.id"), nullable=False),
        sa.Column("post_id", UUID, sa.ForeignKey("posts.id"), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("matched_keyword_names", postgresql.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("report_id", "post_id"),
    )
    op.create_index("ix_personal_report_items_report_id", "personal_report_items", ["report_id"])
    op.create_index("ix_personal_report_items_post_id", "personal_report_items", ["post_id"])

    op.create_table(
        "personal_report_views",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("report_id", UUID, sa.ForeignKey("personal_reports.id"), nullable=False),
        sa.Column("user_id", UUID, sa.ForeignKey("users.id"), nullable=False),
        sa.Column("popup_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("report_id", "user_id"),
    )
    op.create_index("ix_personal_report_views_report_id", "personal_report_views", ["report_id"])
    op.create_index("ix_personal_report_views_user_id", "personal_report_views", ["user_id"])

    op.create_table(
        "review_queue_items",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("organization_id", UUID, sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("post_id", UUID, sa.ForeignKey("posts.id"), nullable=False),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("assigned_to", UUID, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("resolved_by", UUID, sa.ForeignKey("users.id"), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("post_id", "reason"),
    )
    op.create_index("ix_review_queue_items_organization_id", "review_queue_items", ["organization_id"])
    op.create_index("ix_review_queue_items_post_id", "review_queue_items", ["post_id"])


def downgrade() -> None:
    for table in (
        "review_queue_items",
        "personal_report_views",
        "personal_report_items",
        "personal_reports",
        "post_keywords",
        "user_keyword_subscriptions",
        "keywords",
        "news_categories",
    ):
        op.drop_table(table)
