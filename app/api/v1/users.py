from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.permissions import require_admin
from app.models.enums import AccountApprovalStatus
from app.models.user import User
from app.schemas.user import UserAdminRead, UserRoleUpdate
from app.services.user_service import UserService

router = APIRouter()


@router.get("", response_model=list[UserAdminRead])
def list_users(
    approval_status: AccountApprovalStatus | None = None,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return UserService(db).list_users(user.organization_id, approval_status=approval_status)


@router.patch("/{user_id}/approve", response_model=UserAdminRead)
def approve_user(
    user_id: UUID,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return UserService(db).approve(user_id, user.organization_id)


@router.patch("/{user_id}/reject", response_model=UserAdminRead)
def reject_user(
    user_id: UUID,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return UserService(db).reject(user_id, user.organization_id)


@router.patch("/{user_id}/role", response_model=UserAdminRead)
def update_user_role(
    user_id: UUID,
    data: UserRoleUpdate,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return UserService(db).update_role(user_id, user.organization_id, data)
