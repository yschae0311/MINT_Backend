from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.schemas.source import CrawlResult, SourceCreate, SourceRead, SourceUpdate
from app.services.crawler_service import CrawlerService
from app.services.source_service import SourceService

router = APIRouter()


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


@router.post("/{source_id}/crawl", response_model=CrawlResult)
def crawl_source(
    source_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return CrawlerService(db).crawl_source(source_id, user.organization_id)


@router.post("/{source_id}/crawl-to-discovery", response_model=CrawlResult)
def crawl_source_to_discovery(
    source_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Manual pipeline trigger for a single source.
    Always stores results in discovery board (pending) and runs AI summary.
    """
    return CrawlerService(db).crawl_source_to_discovery(source_id, user.organization_id)


@router.post("/crawl-all-to-discovery", response_model=list[CrawlResult])
def crawl_all_to_discovery(
    trusted_only: bool = True,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Manual pipeline trigger for all active sources.
    Defaults to "trusted_only" (trust_level=high).
    """
    return CrawlerService(db).crawl_all_active_to_discovery(user.organization_id, trusted_only=trusted_only)
