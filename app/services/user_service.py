from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestError, NotFoundError
from app.models.enums import AccountApprovalStatus, UserRole
from app.models.user import User
from app.schemas.user import UserAdminRead, UserRoleUpdate


class UserService:
    def __init__(self, db: Session):
        self.db = db

    def list_users(
        self,
        organization_id: UUID,
        *,
        approval_status: AccountApprovalStatus | None = None,
    ) -> list[UserAdminRead]:
        stmt = select(User).where(User.organization_id == organization_id).order_by(User.created_at.desc())
        if approval_status is not None:
            stmt = stmt.where(User.approval_status == approval_status)
        users = self.db.scalars(stmt).all()
        return [UserAdminRead.model_validate(u) for u in users]

    def _get_org_user(self, user_id: UUID, organization_id: UUID) -> User:
        user = self.db.get(User, user_id)
        if not user or user.organization_id != organization_id:
            raise NotFoundError("User not found")
        return user

    def approve(self, user_id: UUID, organization_id: UUID) -> UserAdminRead:
        user = self._get_org_user(user_id, organization_id)
        if user.approval_status != AccountApprovalStatus.pending:
            raise BadRequestError("User is not pending approval")
        user.approval_status = AccountApprovalStatus.approved
        user.is_active = True
        user.role = UserRole.viewer
        self.db.commit()
        self.db.refresh(user)
        return UserAdminRead.model_validate(user)

    def reject(self, user_id: UUID, organization_id: UUID) -> UserAdminRead:
        user = self._get_org_user(user_id, organization_id)
        if user.approval_status != AccountApprovalStatus.pending:
            raise BadRequestError("User is not pending approval")
        user.approval_status = AccountApprovalStatus.rejected
        user.is_active = False
        self.db.commit()
        self.db.refresh(user)
        return UserAdminRead.model_validate(user)

    def update_role(self, user_id: UUID, organization_id: UUID, data: UserRoleUpdate) -> UserAdminRead:
        user = self._get_org_user(user_id, organization_id)
        if user.role == UserRole.admin and data.role != UserRole.admin:
            other_admins = self.db.scalar(
                select(func.count())
                .select_from(User)
                .where(
                    User.organization_id == organization_id,
                    User.role == UserRole.admin,
                    User.id != user_id,
                )
            )
            if not other_admins:
                raise BadRequestError("Cannot remove the last admin")
        user.role = data.role
        self.db.commit()
        self.db.refresh(user)
        return UserAdminRead.model_validate(user)
