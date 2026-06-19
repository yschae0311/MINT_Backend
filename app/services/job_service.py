import logging
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.models.background_job import BackgroundJob
from app.models.enums import JobStatus, JobType
from app.schemas.job import JobRead

logger = logging.getLogger(__name__)

ACTIVE_STATUSES = (JobStatus.pending, JobStatus.running)
FINISHED_STATUSES = (JobStatus.success, JobStatus.failed, JobStatus.cancelled)

BUSY_MESSAGE = "이미 진행 중인 작업이 있습니다. 작업 패널에서 완료를 확인한 후 다시 시도해 주세요."


def _revoke_celery_task(celery_task_id: str | None) -> None:
    if not celery_task_id:
        return
    try:
        from app.workers.celery_app import celery_app

        celery_app.control.revoke(celery_task_id, terminate=True, signal="SIGTERM")
    except Exception as exc:
        logger.warning("Failed to revoke celery task %s: %s", celery_task_id, exc)


class JobService:
    def __init__(self, db: Session):
        self.db = db

    def create_job(
        self,
        organization_id: UUID,
        job_type: JobType,
        label: str,
        *,
        triggered_by: UUID | None = None,
        progress_total: int = 0,
    ) -> BackgroundJob:
        job = BackgroundJob(
            organization_id=organization_id,
            job_type=job_type,
            status=JobStatus.pending,
            label=label,
            progress_total=progress_total,
            triggered_by=triggered_by,
        )
        self.db.add(job)
        self.db.flush()
        return job

    def start_job(self, job_id: UUID) -> None:
        job = self._get(job_id)
        if job.status == JobStatus.cancelled:
            return
        job.status = JobStatus.running
        job.started_at = datetime.now(timezone.utc)
        self.db.commit()

    def is_cancelled(self, job_id: UUID) -> bool:
        job = self._get(job_id)
        return job.status == JobStatus.cancelled

    def update_progress(
        self,
        job_id: UUID,
        current: int,
        total: int | None = None,
        message: str | None = None,
    ) -> None:
        job = self._get(job_id)
        job.progress_current = current
        if total is not None:
            job.progress_total = total
        if message is not None:
            job.progress_message = message
        self.db.commit()

    def complete_job(self, job_id: UUID, result_message: str) -> None:
        job = self._get(job_id)
        if job.status == JobStatus.cancelled:
            return
        job.status = JobStatus.success
        job.result_message = result_message
        job.finished_at = datetime.now(timezone.utc)
        if job.progress_total > 0:
            job.progress_current = job.progress_total
        self.db.commit()

    def fail_job(self, job_id: UUID, error: str) -> None:
        job = self._get(job_id)
        if job.status == JobStatus.cancelled:
            return
        job.status = JobStatus.failed
        job.error = error[:4000]
        job.finished_at = datetime.now(timezone.utc)
        self.db.commit()

    def set_celery_task_id(self, job_id: UUID, task_id: str) -> None:
        job = self._get(job_id)
        job.celery_task_id = task_id
        self.db.commit()

    def get_active_job(self, organization_id: UUID) -> BackgroundJob | None:
        return self.db.scalar(
            select(BackgroundJob)
            .where(
                BackgroundJob.organization_id == organization_id,
                BackgroundJob.status.in_(ACTIVE_STATUSES),
            )
            .order_by(BackgroundJob.created_at.desc())
            .limit(1)
        )

    def require_idle(self, organization_id: UUID) -> None:
        active = self.get_active_job(organization_id)
        if active:
            raise ConflictError(f"{BUSY_MESSAGE} (진행 중: {active.label})")

    def list_jobs(
        self,
        organization_id: UUID,
        *,
        limit: int = 20,
        active_only: bool = False,
    ) -> list[JobRead]:
        q = (
            select(BackgroundJob)
            .where(BackgroundJob.organization_id == organization_id)
            .order_by(BackgroundJob.created_at.desc())
            .limit(limit)
        )
        if active_only:
            q = q.where(BackgroundJob.status.in_(ACTIVE_STATUSES))
        rows = self.db.scalars(q).all()
        return [JobRead.model_validate(r) for r in rows]

    def get_job(self, job_id: UUID, organization_id: UUID) -> JobRead:
        job = self.db.scalar(
            select(BackgroundJob).where(
                BackgroundJob.id == job_id,
                BackgroundJob.organization_id == organization_id,
            )
        )
        if not job:
            raise NotFoundError("Job not found")
        return JobRead.model_validate(job)

    def cancel_job(self, job_id: UUID, organization_id: UUID) -> JobRead:
        job = self._get_org_job(job_id, organization_id)
        if job.status not in ACTIVE_STATUSES:
            raise BadRequestError("취소할 수 없는 작업입니다. (이미 완료·실패·취소됨)")
        job.status = JobStatus.cancelled
        job.finished_at = datetime.now(timezone.utc)
        job.progress_message = "취소됨"
        job.result_message = "사용자에 의해 취소됨"
        _revoke_celery_task(job.celery_task_id)
        self.db.commit()
        return JobRead.model_validate(job)

    def delete_job(self, job_id: UUID, organization_id: UUID) -> None:
        job = self._get_org_job(job_id, organization_id)
        if job.status in ACTIVE_STATUSES:
            raise BadRequestError("진행 중인 작업은 먼저 취소해 주세요.")
        self.db.delete(job)
        self.db.commit()

    def clear_finished_jobs(self, organization_id: UUID) -> int:
        result = self.db.execute(
            delete(BackgroundJob).where(
                BackgroundJob.organization_id == organization_id,
                BackgroundJob.status.in_(FINISHED_STATUSES),
            )
        )
        self.db.commit()
        return result.rowcount or 0

    def _get_org_job(self, job_id: UUID, organization_id: UUID) -> BackgroundJob:
        job = self.db.scalar(
            select(BackgroundJob).where(
                BackgroundJob.id == job_id,
                BackgroundJob.organization_id == organization_id,
            )
        )
        if not job:
            raise NotFoundError("Job not found")
        return job

    def _get(self, job_id: UUID) -> BackgroundJob:
        job = self.db.get(BackgroundJob, job_id)
        if not job:
            raise NotFoundError("Job not found")
        return job


def dispatch_task(task, job_id: str, *args, db: Session) -> None:
    """Enqueue Celery task; fall back to inline execution if broker is unavailable."""
    try:
        async_result = task.delay(job_id, *args)
        JobService(db).set_celery_task_id(UUID(job_id), async_result.id)
    except Exception as exc:
        logger.warning("Celery dispatch failed, running inline job=%s: %s", job_id, exc)
        task(job_id, *args)
