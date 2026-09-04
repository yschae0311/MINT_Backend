from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.common import ORMBase


class EditionRead(ORMBase):
    id: UUID
    organization_id: UUID
    slug: str
    name: str
    sort_order: int
    is_active: bool
    topic_terms: list[str] = Field(default_factory=list)
    tagged_source_count: int = 0
    featured_keyword_count: int = 0
    missing_sources: bool = False
    created_at: datetime
    updated_at: datetime


class EditionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    slug: str | None = Field(default=None, max_length=64, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    topic_terms: list[str] = Field(default_factory=list, max_length=40)
    sort_order: int | None = None
    is_active: bool = True


class EditionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    topic_terms: list[str] | None = Field(default=None, max_length=40)
    sort_order: int | None = None
    is_active: bool | None = None


class FeaturedKeywordsUpdate(BaseModel):
    keyword_ids: list[UUID] = Field(default_factory=list, max_length=40)
