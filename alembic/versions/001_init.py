"""init schema

Revision ID: 001
Revises:
Create Date: 2026-06-04

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("industry", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("role", sa.Enum("admin", "manager", "member", "viewer", name="user_role"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    source_type = sa.Enum("rss", "webpage", "news_page", "notice_page", "manual", name="source_type")
    trust_level = sa.Enum("high", "medium", "low", name="trust_level")
    discovery_type = sa.Enum("manual", "ai_discovered", "user_submitted", name="discovery_type")
    source_type.create(op.get_bind(), checkfirst=True)
    trust_level.create(op.get_bind(), checkfirst=True)
    discovery_type.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "sources",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("source_type", source_type, nullable=False),
        sa.Column("industry", sa.String(128), nullable=False),
        sa.Column("category", sa.String(128), nullable=False),
        sa.Column("trust_level", trust_level, nullable=False),
        sa.Column("reliability_score", sa.Integer(), nullable=False),
        sa.Column("discovery_type", discovery_type, nullable=False),
        sa.Column("auto_publish", sa.Boolean(), server_default="true"),
        sa.Column("crawl_frequency", sa.String(64), nullable=False),
        sa.Column("last_crawled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true"),
        sa.Column("approved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    board_type = sa.Enum("trusted", "discovery", name="board_type")
    post_status = sa.Enum("pending", "published", "hidden", "deleted", "promoted", name="post_status")
    post_trust = sa.Enum("high", "medium", "low", name="post_trust_level")
    importance = sa.Enum("high", "medium", "low", "unknown", name="importance")
    created_by = sa.Enum("admin", "crawler", "ai_discovery", name="created_by")
    for e in (board_type, post_status, post_trust, importance, created_by):
        e.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "posts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("sources.id"), nullable=True),
        sa.Column("board_type", board_type, nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("original_url", sa.String(2048), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("collected_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("raw_content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("category", sa.String(128), nullable=True),
        sa.Column("keywords", postgresql.JSON(), nullable=True),
        sa.Column("status", post_status, nullable=False),
        sa.Column("trust_level", post_trust, nullable=False),
        sa.Column("reliability_score", sa.Integer(), nullable=False),
        sa.Column("importance", importance, nullable=False),
        sa.Column("created_by", created_by, nullable=False),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_posts_content_hash", "posts", ["content_hash"])

    ai_importance = sa.Enum("high", "medium", "low", name="ai_importance")
    ai_importance.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "ai_outputs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("post_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("posts.id"), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("impact", sa.Text(), nullable=True),
        sa.Column("action_items", postgresql.JSON(), nullable=True),
        sa.Column("importance", ai_importance, nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("prompt_version", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    op.create_table(
        "daily_reports",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("title", sa.String(512), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("key_changes", postgresql.JSON(), nullable=True),
        sa.Column("risks", postgresql.JSON(), nullable=True),
        sa.Column("action_items", postgresql.JSON(), nullable=True),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("prompt_version", sa.String(32), nullable=False),
        sa.Column("slack_sent", sa.Boolean(), server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    report_item_importance = sa.Enum("high", "medium", "low", name="report_item_importance")
    report_item_importance.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "daily_report_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("report_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("daily_reports.id"), nullable=False),
        sa.Column("post_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("posts.id"), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("importance", report_item_importance, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    slack_purpose = sa.Enum("daily", "urgent", "review", "all", name="slack_purpose")
    slack_purpose.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "slack_webhooks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("webhook_url_encrypted", sa.Text(), nullable=False),
        sa.Column("channel_name", sa.String(128), nullable=False),
        sa.Column("purpose", slack_purpose, nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    channel_type = sa.Enum("slack", "email", "telegram", name="channel_type")
    notification_status = sa.Enum("success", "failed", "pending", name="notification_status")
    channel_type.create(op.get_bind(), checkfirst=True)
    notification_status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "notification_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("channel_type", channel_type, nullable=False),
        sa.Column("channel_name", sa.String(128), nullable=False),
        sa.Column("post_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("report_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("status", notification_status, nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("notification_logs")
    op.drop_table("slack_webhooks")
    op.drop_table("daily_report_items")
    op.drop_table("daily_reports")
    op.drop_table("ai_outputs")
    op.drop_table("posts")
    op.drop_table("sources")
    op.drop_table("users")
    op.drop_table("organizations")
