import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.columns import str_enum
from app.models.enums import SlackPurpose


class SlackWebhook(Base):
    __tablename__ = "slack_webhooks"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False
    )
    webhook_url_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    channel_name: Mapped[str] = mapped_column(String(128), default="#ev-intel")
    purpose: Mapped[SlackPurpose] = mapped_column(str_enum(SlackPurpose, "slack_purpose"), default=SlackPurpose.all)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
