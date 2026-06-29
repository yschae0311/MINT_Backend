from uuid import UUID

from pydantic import BaseModel, Field

from app.models.enums import BoardType
from app.schemas.common import ORMBase


class SearchPostHit(BaseModel):
    id: UUID
    title: str
    board_type: BoardType
    source_name: str | None = None
    summary: str | None = None
    original_url: str | None = None
    title_highlight: str | None = None
    summary_highlight: str | None = None


class SearchSourceHit(ORMBase):
    id: UUID
    name: str
    url: str
    category: str | None = None


class GlobalSearchResponse(BaseModel):
    query: str
    posts: list[SearchPostHit] = []
    sources: list[SearchSourceHit] = []


class GlobalSearchParams(BaseModel):
    q: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=8, ge=1, le=20)
