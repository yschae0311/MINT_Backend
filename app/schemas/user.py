from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.models.enums import AccountApprovalStatus, UserRole
from app.schemas.common import ORMBase


class UserAdminRead(ORMBase):
    id: UUID
    organization_id: UUID
    email: str
    name: str
    role: UserRole
    approval_status: AccountApprovalStatus
    is_active: bool
    created_at: datetime


class UserRoleUpdate(BaseModel):
    role: UserRole
