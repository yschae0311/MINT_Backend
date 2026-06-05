from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.enums import BoardType, CreatedBy, Importance, PostStatus, TrustLevel
from app.schemas.common import ORMBase


class AIOutputRead(ORMBase):
    id: UUID
    summary: str
    impact: str | None
    action_items: list | None
    importance: Importance
    confidence: float | None
    model: str
    prompt_version: str
    created_at: datetime


class PostBase(BaseModel):
    title: str
    original_url: str | None = None
    raw_content: str = ""
    board_type: BoardType = BoardType.trusted
    category: str | None = None
    importance: Importance = Importance.unknown


class PostCreate(PostBase):
    source_id: UUID | None = None
    status: PostStatus | None = None


class PostUpdate(BaseModel):
    title: str | None = None
    category: str | None = None
    importance: Importance | None = None
    status: PostStatus | None = None


class PostRead(ORMBase):
    id: UUID
    organization_id: UUID
    source_id: UUID | None
    source_name: str | None = None
    board_type: BoardType
    title: str
    original_url: str | None
    published_at: datetime | None
    collected_at: datetime
    raw_content: str
    category: str | None
    status: PostStatus
    trust_level: TrustLevel
    reliability_score: int
    importance: Importance
    created_by: CreatedBy
    created_at: datetime
    updated_at: datetime
    latest_ai: AIOutputRead | None = None


class PostDetail(PostRead):
    keywords: dict | list | None = None
    ai_outputs: list[AIOutputRead] = []


class PostListParams(BaseModel):
    board_type: BoardType | None = None
    status: PostStatus | None = None
    importance: Importance | None = None
    category: str | None = None
    keyword: str | None = None
    source_id: UUID | None = None
    page: int = Field(default=1, ge=1)
    size: int = Field(default=20, ge=1, le=100)
