from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.permissions import require_admin, require_inquiry_submitter
from app.core.security import get_current_user
from app.models.enums import InquiryStatus
from app.models.user import User
from app.schemas.inquiry import InquiryCreate, InquiryDetail, InquiryMessageCreate, InquiryRead
from app.services.inquiry_service import InquiryService

router = APIRouter()


@router.post("", response_model=InquiryDetail)
def create_inquiry(
    data: InquiryCreate,
    user: User = Depends(require_inquiry_submitter),
    db: Session = Depends(get_db),
):
    return InquiryService(db).create_inquiry(user.organization_id, user, data)


@router.get("/mine", response_model=list[InquiryRead])
def list_my_inquiries(
    user: User = Depends(require_inquiry_submitter),
    db: Session = Depends(get_db),
):
    return InquiryService(db).list_mine(user.organization_id, user)


@router.get("", response_model=list[InquiryRead])
def list_inquiries(
    status: InquiryStatus | None = None,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return InquiryService(db).list_all(user.organization_id, status=status)


@router.get("/open-count")
def open_inquiry_count(
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return {"count": InquiryService(db).count_open(user.organization_id)}


@router.get("/{inquiry_id}", response_model=InquiryDetail)
def get_inquiry(
    inquiry_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return InquiryService(db).get_detail(inquiry_id, user.organization_id, user)


@router.post("/{inquiry_id}/messages", response_model=InquiryDetail)
def add_inquiry_message(
    inquiry_id: UUID,
    data: InquiryMessageCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return InquiryService(db).add_message(inquiry_id, user.organization_id, user, data)


@router.patch("/{inquiry_id}/close", response_model=InquiryDetail)
def close_inquiry(
    inquiry_id: UUID,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return InquiryService(db).close(inquiry_id, user.organization_id)
