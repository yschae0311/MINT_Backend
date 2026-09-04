from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.permissions import require_admin
from app.models.user import User
from app.schemas.user import UserActiveUpdate, UserAdminRead, UserEditionsUpdate, UserRoleUpdate
from app.services.user_service import UserService

router = APIRouter()


@router.get("", response_model=list[UserAdminRead])
def list_users(
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return UserService(db).list_users(user.organization_id)


@router.patch("/{user_id}/role", response_model=UserAdminRead)
def update_user_role(
    user_id: UUID,
    data: UserRoleUpdate,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return UserService(db).update_role(user_id, user.organization_id, data, actor=user)


@router.patch("/{user_id}/active", response_model=UserAdminRead)
def update_user_active(
    user_id: UUID,
    data: UserActiveUpdate,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return UserService(db).set_active(user_id, user.organization_id, data.is_active, actor=user)


@router.put("/{user_id}/editions", response_model=UserAdminRead)
def update_user_editions(
    user_id: UUID,
    data: UserEditionsUpdate,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return UserService(db).set_editions(user_id, user.organization_id, data.editions)
