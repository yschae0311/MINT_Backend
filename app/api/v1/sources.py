from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.exceptions import NotFoundError
from app.core.security import get_current_user
from app.models.enums import JobType
from app.models.source import Source
from app.models.user import User
from app.schemas.job import JobRead
from app.schemas.source import (
    CollectionSettingsRead,
    CollectionSettingsUpdate,
    SourceCreate,
    SourceRead,
    SourceUpdate,
)
from app.services.job_service import JobService, dispatch_task
from app.services.org_settings_service import OrgSettingsService
from app.services.source_service import SourceService
from app.workers.tasks import crawl_all_discovery_job_task, crawl_source_job_task

router = APIRouter()


@router.get("/collection-settings", response_model=CollectionSettingsRead)
def get_collection_settings(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return OrgSettingsService(db).get_collection_settings(user.organization_id)


@router.patch("/collection-settings", response_model=CollectionSettingsRead)
def update_collection_settings(
    data: CollectionSettingsUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return OrgSettingsService(db).update_collection_settings(
        user.organization_id,
        discovery_pending_retention_days=data.discovery_pending_retention_days,
    )


@router.get("", response_model=list[SourceRead])
def list_sources(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return SourceService(db).list_sources(user.organization_id)


@router.post("", response_model=SourceRead)
def create_source(
    data: SourceCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return SourceService(db).create_source(user.organization_id, data)


@router.get("/{source_id}", response_model=SourceRead)
def get_source(
    source_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return SourceService(db).get_source(source_id, user.organization_id)


@router.patch("/{source_id}", response_model=SourceRead)
def update_source(
    source_id: UUID,
    data: SourceUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return SourceService(db).update_source(source_id, user.organization_id, data)


@router.delete("/{source_id}", status_code=204)
def delete_source(
    source_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    SourceService(db).delete_source(source_id, user.organization_id)


@router.post("/{source_id}/crawl", response_model=JobRead, status_code=status.HTTP_202_ACCEPTED)
def crawl_source(
    source_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    source = db.get(Source, source_id)
    if not source or source.organization_id != user.organization_id:
        raise NotFoundError("Source not found")
    jobs = JobService(db)
    jobs.require_idle(user.organization_id)
    job = jobs.create_job(
        user.organization_id,
        JobType.crawl_source,
        f"소스 크롤링 · {source.name}",
        triggered_by=user.id,
        progress_total=1,
    )
    db.commit()
    dispatch_task(
        crawl_source_job_task,
        str(job.id),
        str(source_id),
        str(user.organization_id),
        False,
        db=db,
    )
    return JobRead.model_validate(job)


@router.post(
    "/{source_id}/crawl-to-discovery",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def crawl_source_to_discovery(
    source_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    source = db.get(Source, source_id)
    if not source or source.organization_id != user.organization_id:
        raise NotFoundError("Source not found")
    jobs = JobService(db)
    jobs.require_idle(user.organization_id)
    job = jobs.create_job(
        user.organization_id,
        JobType.crawl_source_discovery,
        f"AI 발견 크롤링 · {source.name}",
        triggered_by=user.id,
        progress_total=1,
    )
    db.commit()
    dispatch_task(
        crawl_source_job_task,
        str(job.id),
        str(source_id),
        str(user.organization_id),
        True,
        db=db,
    )
    return JobRead.model_validate(job)


@router.post(
    "/crawl-all-to-discovery",
    response_model=JobRead,
    status_code=status.HTTP_202_ACCEPTED,
)
def crawl_all_to_discovery(
    trusted_only: bool = True,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    jobs = JobService(db)
    jobs.require_idle(user.organization_id)
    job = jobs.create_job(
        user.organization_id,
        JobType.crawl_all_discovery,
        "전체 AI 발견 파이프라인",
        triggered_by=user.id,
    )
    db.commit()
    dispatch_task(
        crawl_all_discovery_job_task,
        str(job.id),
        str(user.organization_id),
        trusted_only,
        db=db,
    )
    return JobRead.model_validate(job)
