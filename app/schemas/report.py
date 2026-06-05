from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel

from app.models.enums import Importance
from app.schemas.common import ORMBase
from app.schemas.post import PostRead


class ReportGenerateRequest(BaseModel):
    report_date: date | None = None


class DailyReportItemRead(ORMBase):
    id: UUID
    post_id: UUID
    reason: str | None
    importance: Importance
    post: PostRead | None = None


class DailyReportRead(ORMBase):
    id: UUID
    organization_id: UUID
    report_date: date
    title: str
    summary: str
    key_changes: list | dict | None
    risks: list | None
    action_items: list | None
    model: str
    slack_sent: bool
    created_at: datetime
    updated_at: datetime


class DailyReportDetail(DailyReportRead):
    items: list[DailyReportItemRead] = []
