from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.permissions import require_admin, require_edition_editor
from app.core.security import get_current_user
from app.models.user import User
from app.schemas.edition import EditionCreate, EditionRead, EditionUpdate, FeaturedKeywordsUpdate
from app.schemas.personalization import KeywordRead
from app.services.edition_service import EditionService
from app.services.membership_service import MembershipService
from app.services.personalization_service import TaxonomyService

router = APIRouter()


@router.get("/available", response_model=list[EditionRead])
def list_available_editions(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Active desks in the org, not filtered by the caller's membership."""
    service = EditionService(db)
    TaxonomyService(db).ensure_defaults(user.organization_id)
    rows = service.list_reads(user.organization_id, active_only=True)
    db.commit()
    return rows


@router.get("", response_model=list[EditionRead])
def list_editions(
    active_only: bool = Query(default=True),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    service = EditionService(db)
    TaxonomyService(db).ensure_defaults(user.organization_id)
    rows = service.list_reads(user.organization_id, active_only=active_only)
    visible = MembershipService(db).visible_edition_ids(user, active_only=False)
    if visible is not None:
        rows = [row for row in rows if row.id in visible]
    db.commit()
    return rows


@router.post("", response_model=EditionRead, status_code=status.HTTP_201_CREATED)
def create_edition(
    data: EditionCreate,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    service = EditionService(db)
    row = service.create(user.organization_id, data)
    return service.to_read(row, untagged_sources=service.untagged_active_source_count(user.organization_id))


@router.patch("/{edition_id}", response_model=EditionRead)
def update_edition(
    edition_id: UUID,
    data: EditionUpdate,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    service = EditionService(db)
    row = service.update(edition_id, user.organization_id, data)
    tagged = service.tagged_source_counts(user.organization_id).get(row.id, 0)
    featured = service.featured_keyword_counts(user.organization_id).get(row.id, 0)
    return service.to_read(
        row,
        tagged_sources=tagged,
        featured_keywords=featured,
        untagged_sources=service.untagged_active_source_count(user.organization_id),
    )


@router.put("/{edition_id}/keywords/featured", response_model=list[KeywordRead])
def update_featured_keywords(
    edition_id: UUID,
    data: FeaturedKeywordsUpdate,
    user: User = Depends(require_edition_editor),
    db: Session = Depends(get_db),
):
    rows = EditionService(db).set_featured_keywords(
        user.organization_id, edition_id, data.keyword_ids
    )
    return [KeywordRead.model_validate(row) for row in rows]
