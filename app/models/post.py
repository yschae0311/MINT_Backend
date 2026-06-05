import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.columns import str_enum
from app.models.enums import BoardType, CreatedBy, Importance, PostStatus, TrustLevel


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("organizations.id"), nullable=False
    )
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sources.id"), nullable=True
    )
    board_type: Mapped[BoardType] = mapped_column(str_enum(BoardType, "board_type"), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    original_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    raw_content: Mapped[str] = mapped_column(Text, default="")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    keywords: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[PostStatus] = mapped_column(str_enum(PostStatus, "post_status"), default=PostStatus.pending)
    trust_level: Mapped[TrustLevel] = mapped_column(str_enum(TrustLevel, "post_trust_level"), default=TrustLevel.medium)
    reliability_score: Mapped[int] = mapped_column(Integer, default=50)
    importance: Mapped[Importance] = mapped_column(str_enum(Importance, "importance"), default=Importance.unknown)
    created_by: Mapped[CreatedBy] = mapped_column(str_enum(CreatedBy, "created_by"), default=CreatedBy.admin)
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    organization = relationship("Organization", back_populates="posts")
    source = relationship("Source", back_populates="posts")
    ai_outputs = relationship("AIOutput", back_populates="post")
