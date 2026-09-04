from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.enums import AccountApprovalStatus, UserRole
from app.schemas.auth import UserEditionMembership
from app.schemas.common import ORMBase


class UserAdminRead(ORMBase):
    id: UUID
    organization_id: UUID
    email: str
    name: str
    role: UserRole
    approval_status: AccountApprovalStatus
    is_active: bool
    last_login_at: datetime | None = None
    created_at: datetime
    editions: list[UserEditionMembership] = Field(default_factory=list)


class UserRoleUpdate(BaseModel):
    role: UserRole


class UserEditionAssignment(BaseModel):
    edition_id: UUID
    is_editor: bool = False


class UserEditionsUpdate(BaseModel):
    editions: list[UserEditionAssignment] = Field(default_factory=list)


class UserActiveUpdate(BaseModel):
    is_active: bool


class MyEditionsUpdate(BaseModel):
    edition_ids: list[UUID] = Field(min_length=1, max_length=20)

