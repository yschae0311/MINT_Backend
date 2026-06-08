from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.schemas.job import JobRead
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


@router.get("/{job_id}", response_model=JobRead)
def get_job(
    job_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return JobService(db).get_job(job_id, user.organization_id)
