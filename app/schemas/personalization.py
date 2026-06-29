from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.enums import (
    Importance,
    KeywordScope,
    KeywordStatus,
    ReviewQueueReason,
    ReviewQueueStatus,
)
from app.schemas.common import ORMBase


class CategoryRead(ORMBase):
    id: UUID
    name: str
    sort_order: int


class CategoryWrite(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    sort_order: int = 0


class KeywordRead(ORMBase):
    id: UUID
    category_id: UUID | None
    owner_user_id: UUID | None
    name: str
    normalized_name: str
    aliases: list[str] | None
    scope: KeywordScope
    status: KeywordStatus
    usage_count: int
    selected: bool = False


class KeywordCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    category_id: UUID | None = None
    aliases: list[str] = Field(default_factory=list, max_length=20)


class KeywordUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    category_id: UUID | None = None
    aliases: list[str] | None = Field(default=None, max_length=20)
    status: KeywordStatus | None = None


class KeywordMergeRequest(BaseModel):
    source_keyword_id: UUID


class KeywordSubscriptionUpdate(BaseModel):
    keyword_ids: list[UUID] = Field(min_length=3, max_length=30)


class MatchedKeyword(BaseModel):
    id: UUID
    name: str
    confidence: float


class NewsItem(BaseModel):
    id: UUID
    title: str
    source_name: str | None
    category: str | None
    collected_at: datetime
    original_url: str | None
    importance: Importance
    summary: str | None
    summary_highlight: str | None = None
    matched_keywords: list[MatchedKeyword]
    personalization_score: float = 0


class NewsPage(BaseModel):
    items: list[NewsItem]
    total: int
    page: int
    size: int
    pages: int


class PersonalReportItemRead(BaseModel):
    post: NewsItem
    rank: int
    score: float
    matched_keyword_names: list[str]


class PersonalReportRead(BaseModel):
    id: UUID
    report_date: date
    title: str
    summary: str
    item_count: int
    popup_seen: bool = False
    items: list[PersonalReportItemRead] = Field(default_factory=list)


class PersonalReportViewUpdate(BaseModel):
    popup_seen: bool = True
    opened: bool = False


class ReviewQueueRead(BaseModel):
    id: UUID
    post_id: UUID
    post_title: str
    reason: ReviewQueueReason
    status: ReviewQueueStatus
    detail: str | None
    created_at: datetime


class ReviewQueueResolve(BaseModel):
    status: ReviewQueueStatus
    detail: str | None = Field(default=None, max_length=1000)


class KeywordSuggestion(BaseModel):
    name: str
    confidence: float
    keyword_id: UUID | None = None


class KeywordSuggestResponse(BaseModel):
    post_id: UUID
    category: str | None
    suggestions: list[KeywordSuggestion]


class ReviewQueueKeywordsApply(BaseModel):
    keyword_ids: list[UUID] = Field(default_factory=list, max_length=5)
    new_keyword_names: list[str] = Field(default_factory=list, max_length=5)
    category: str | None = Field(default=None, max_length=128)


class ReviewQueueKeywordsApplyResponse(BaseModel):
    post_id: UUID
    linked_keywords: list[str]
    resolved_queue_item_ids: list[UUID]


class ReclassifyResponse(BaseModel):
    post_id: UUID
    category: str | None
    keywords: list[str]
    review_reasons: list[ReviewQueueReason]
