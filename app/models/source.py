import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.columns import str_enum
from app.models.enums import DiscoveryType, SourceType, TrustLevel


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    source_type: Mapped[SourceType] = mapped_column(str_enum(SourceType, "source_type"), default=SourceType.rss)
    industry: Mapped[str] = mapped_column(String(128), default="EV")
    category: Mapped[str] = mapped_column(String(128), default="general")
    trust_level: Mapped[TrustLevel] = mapped_column(str_enum(TrustLevel, "trust_level"), default=TrustLevel.high)
    reliability_score: Mapped[int] = mapped_column(Integer, default=80)
    discovery_type: Mapped[DiscoveryType] = mapped_column(
        str_enum(DiscoveryType, "discovery_type"), default=DiscoveryType.manual
    )
    auto_publish: Mapped[bool] = mapped_column(Boolean, default=True)
    crawl_frequency: Mapped[str] = mapped_column(String(64), default="daily")
    last_crawled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    organization = relationship("Organization", back_populates="sources")
    posts = relationship("Post", back_populates="source")
