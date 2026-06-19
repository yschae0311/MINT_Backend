from datetime import datetime

from pydantic import BaseModel

from app.models.enums import BoardType, Importance, PostStatus


class DashboardPostPreview(BaseModel):
    id: str
    title: str
    source_name: str | None
    board_type: BoardType
    status: PostStatus
    importance: Importance
    collected_at: datetime
    original_url: str | None
    ai_summary: str | None = None


class DashboardReportHighlight(BaseModel):
    title: str
    description: str | None = None
    importance: str | None = None


class DashboardLatestReport(BaseModel):
    id: str
    title: str
    report_date: str
    summary: str
    slack_sent: bool
    highlights: list[DashboardReportHighlight] = []


class DashboardStatsResponse(BaseModel):
    new_today: int
    trusted_count: int
    pending_discovery: int
    high_importance: int
    active_sources: int
    total_sources: int
    latest_report: DashboardLatestReport | None
    trusted_preview: list[DashboardPostPreview]
    discovery_preview: list[DashboardPostPreview]
