from fastapi import Depends, HTTPException, status

from app.core.security import get_current_user
from app.models.enums import UserRole
from app.models.user import User

ADMIN_ROLES = {UserRole.admin}
WRITE_ROLES = {UserRole.admin}
INQUIRY_SUBMIT_ROLES = {UserRole.manager, UserRole.member, UserRole.viewer}


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role not in ADMIN_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
    return user


def require_inquiry_submitter(user: User = Depends(get_current_user)) -> User:
    if user.role not in INQUIRY_SUBMIT_ROLES:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions")
    return user
