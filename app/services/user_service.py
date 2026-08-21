from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestError, NotFoundError
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.user import UserAdminRead, UserEditionAssignment, UserRoleUpdate
from app.services.membership_service import MembershipService


class UserService:
    def __init__(self, db: Session):
        self.db = db

    def list_users(self, organization_id: UUID) -> list[UserAdminRead]:
        membership = MembershipService(self.db)
        users = self.db.scalars(
            select(User).where(User.organization_id == organization_id).order_by(User.created_at.desc())
        ).all()
        return [membership.to_admin_read(user) for user in users]

    def _get_org_user(self, user_id: UUID, organization_id: UUID) -> User:
        user = self.db.get(User, user_id)
        if not user or user.organization_id != organization_id:
            raise NotFoundError("User not found")
        return user

    def update_role(self, user_id: UUID, organization_id: UUID, data: UserRoleUpdate) -> UserAdminRead:
        user = self._get_org_user(user_id, organization_id)
        if user.role == UserRole.admin and data.role != UserRole.admin:
            self._ensure_other_admin(organization_id, user_id)
        user.role = data.role
        self.db.commit()
        self.db.refresh(user)
        return MembershipService(self.db).to_admin_read(user)

    def set_active(self, user_id: UUID, organization_id: UUID, is_active: bool) -> UserAdminRead:
        user = self._get_org_user(user_id, organization_id)
        if user.role == UserRole.admin and not is_active:
            self._ensure_other_admin(organization_id, user_id)
        user.is_active = is_active
        self.db.commit()
        self.db.refresh(user)
        return MembershipService(self.db).to_admin_read(user)

    def set_editions(
        self,
        user_id: UUID,
        organization_id: UUID,
        assignments: list[UserEditionAssignment],
    ) -> UserAdminRead:
        user = self._get_org_user(user_id, organization_id)
        MembershipService(self.db).set_user_editions(user, assignments)
        return MembershipService(self.db).to_admin_read(user)

    def _ensure_other_admin(self, organization_id: UUID, user_id: UUID) -> None:
        other_admins = self.db.scalar(
            select(func.count())
            .select_from(User)
            .where(
                User.organization_id == organization_id,
                User.role == UserRole.admin,
                User.is_active.is_(True),
                User.id != user_id,
            )
        )
        if not other_admins:
            raise BadRequestError("Cannot remove the last admin")
