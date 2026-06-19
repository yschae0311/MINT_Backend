from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.schemas.job import JobClearResponse, JobRead
from app.services.job_service import JobService

router = APIRouter()


@router.get("", response_model=list[JobRead])
def list_jobs(
    active_only: bool = Query(False),
    limit: int = Query(20, ge=1, le=50),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return JobService(db).list_jobs(user.organization_id, limit=limit, active_only=active_only)


@router.delete("/finished", response_model=JobClearResponse)
def clear_finished_jobs(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    deleted = JobService(db).clear_finished_jobs(user.organization_id)
    return JobClearResponse(deleted=deleted)


@router.get("/{job_id}", response_model=JobRead)
def get_job(
    job_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return JobService(db).get_job(job_id, user.organization_id)


@router.post("/{job_id}/cancel", response_model=JobRead)
def cancel_job(
    job_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return JobService(db).cancel_job(job_id, user.organization_id)


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(
    job_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    JobService(db).delete_job(job_id, user.organization_id)
