from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.enums import DiscoveryType, SourceType, TrustLevel
from app.schemas.common import ORMBase


class SourceBase(BaseModel):
    name: str
    url: str
    source_type: SourceType = SourceType.rss
    industry: str = "EV"
    category: str = "general"
    trust_level: TrustLevel = TrustLevel.high
    reliability_score: int = Field(default=80, ge=0, le=100)
    discovery_type: DiscoveryType = DiscoveryType.manual
    auto_publish: bool = True
    crawl_frequency: str = "daily"
    is_active: bool = True


class SourceCreate(SourceBase):
    pass


class SourceUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    source_type: SourceType | None = None
    industry: str | None = None
    category: str | None = None
    trust_level: TrustLevel | None = None
    reliability_score: int | None = Field(default=None, ge=0, le=100)
    auto_publish: bool | None = None
    crawl_frequency: str | None = None
    is_active: bool | None = None


class SourceRead(ORMBase):
    id: UUID
    organization_id: UUID
    name: str
    url: str
    source_type: SourceType
    industry: str
    category: str
    trust_level: TrustLevel
    reliability_score: int
    discovery_type: DiscoveryType
    auto_publish: bool
    crawl_frequency: str
    last_crawled_at: datetime | None
    is_active: bool
    created_at: datetime
    updated_at: datetime


class CrawlResult(BaseModel):
    source_id: UUID
    created: int
    skipped: int
    message: str
    error: str | None = None
    skip_reasons: dict[str, int] = Field(default_factory=dict)
    error_sample: str | None = None


class CollectionSettingsRead(BaseModel):
    discovery_pending_retention_days: int
    default_retention_days: int
    is_custom: bool


class CollectionSettingsUpdate(BaseModel):
    discovery_pending_retention_days: int = Field(ge=0, le=365)


class CommunityUrlSubmit(BaseModel):
    url: str = Field(min_length=8, max_length=2048)
    title: str | None = Field(default=None, max_length=512)
    note: str = Field(default="", max_length=2000)


class CommunityUrlSubmitResult(BaseModel):
    accepted: bool
    reason: str | None = None
