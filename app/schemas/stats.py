from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.enums import BoardType, Importance, PostStatus


class DashboardPostPreview(BaseModel):
    id: str
    title: str
    source_name: str | None
    source_type: str | None = None
    board_type: BoardType
    status: PostStatus
    importance: Importance
    collected_at: datetime
    original_url: str | None
    ai_summary: str | None = None
    image_url: str | None = None


class DashboardReportHighlight(BaseModel):
    title: str
    description: str | None = None
    importance: str | None = None
    related_post_ids: list[str] = []


class DashboardLatestReport(BaseModel):
    id: str
    title: str
    report_date: str
    summary: str
    slack_sent: bool
    illustration_url: str | None = None
    highlights: list[DashboardReportHighlight] = []


class DashboardStatsResponse(BaseModel):
    new_today: int
    trusted_count: int
    pending_discovery: int
    review_queue_pending: int
    high_importance: int
    active_sources: int
    total_sources: int
    discovery_pending_retention_days: int
    latest_report: DashboardLatestReport | None
    trusted_preview: list[DashboardPostPreview]
    discovery_preview: list[DashboardPostPreview]
    community_voices_preview: list[DashboardPostPreview] = []


class FrontPhotoRequest(BaseModel):
    report_id: UUID | None = None
    title: str | None = Field(default=None, max_length=300)
    summary: str | None = Field(default=None, max_length=800)
    seed: str | None = Field(default=None, max_length=120)
    force: bool = False


class FrontPhotoResponse(BaseModel):
    illustration_url: str


class WeatherResponse(BaseModel):
    location: str
    temperature_c: float
    feels_like_c: float | None = None
    humidity_pct: int | None = None
    wind_kmh: float | None = None
    condition: str
    high_c: float | None = None
    low_c: float | None = None
    weather_code: int
