from datetime import datetime
from uuid import UUID

from app.models.enums import JobStatus, JobType
from app.schemas.common import ORMBase


class JobRead(ORMBase):
    id: UUID
    organization_id: UUID
    job_type: JobType
    status: JobStatus
    label: str
    progress_current: int
    progress_total: int
    progress_message: str | None
    result_message: str | None
    error: str | None
    triggered_by: UUID | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
