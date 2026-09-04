import os
import unittest
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.core.database import Base
import app.models  # noqa: F401
from app.core.exceptions import BadRequestError
from app.models.enums import AccountApprovalStatus, UserRole
from app.models.organization import Organization
from app.models.user import User
from app.schemas.user import UserRoleUpdate
from app.services.user_service import UserService


class UserServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._prev = os.environ.get("SEARCH_BACKEND")
        os.environ["SEARCH_BACKEND"] = "postgres"
        get_settings.cache_clear()
        self.engine = create_engine(
            "sqlite:///:memory:",
            execution_options={"schema_translate_map": {"mint": None}},
        )
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        self.db = self.Session()
        org = Organization(name="Test", industry="EV")
        self.db.add(org)
        self.db.flush()
        self.org_id = org.id
        self.admin = self._user("admin@example.com", UserRole.admin, "총관")
        self.member = self._user("member@example.com", UserRole.viewer, "멤버")
        self.db.commit()
        self.users = UserService(self.db)

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()
        if self._prev is None:
            os.environ.pop("SEARCH_BACKEND", None)
        else:
            os.environ["SEARCH_BACKEND"] = self._prev
        get_settings.cache_clear()

    def _user(self, email: str, role: UserRole, name: str) -> User:
        user = User(
            organization_id=self.org_id,
            email=email,
            password_hash="x",
            name=name,
            role=role,
            approval_status=AccountApprovalStatus.approved,
            is_active=True,
        )
        self.db.add(user)
        self.db.flush()
        return user

    def test_promote_and_demote_admin(self) -> None:
        self.users.update_role(
            self.member.id,
            self.org_id,
            UserRoleUpdate(role=UserRole.admin),
            actor=self.admin,
        )
        self.db.refresh(self.member)
        self.assertEqual(self.member.role, UserRole.admin)
        self.users.update_role(
            self.member.id,
            self.org_id,
            UserRoleUpdate(role=UserRole.viewer),
            actor=self.admin,
        )
        self.db.refresh(self.member)
        self.assertEqual(self.member.role, UserRole.viewer)

    def test_cannot_demote_self(self) -> None:
        with self.assertRaises(BadRequestError):
            self.users.update_role(
                self.admin.id,
                self.org_id,
                UserRoleUpdate(role=UserRole.viewer),
                actor=self.admin,
            )

    def test_cannot_deactivate_self(self) -> None:
        with self.assertRaises(BadRequestError):
            self.users.set_active(self.admin.id, self.org_id, False, actor=self.admin)

    def test_cannot_remove_last_admin(self) -> None:
        with self.assertRaises(BadRequestError):
            self.users.update_role(
                self.admin.id,
                self.org_id,
                UserRoleUpdate(role=UserRole.viewer),
                actor=self.member,
            )

    def test_admin_read_includes_last_login(self) -> None:
        read = self.users.list_users(self.org_id)
        row = next(item for item in read if item.id == self.member.id)
        self.assertIsNone(row.last_login_at)

    def test_foreign_org_user_not_found(self) -> None:
        from app.core.exceptions import NotFoundError

        with self.assertRaises(NotFoundError):
            self.users.update_role(
                uuid4(),
                self.org_id,
                UserRoleUpdate(role=UserRole.admin),
                actor=self.admin,
            )


if __name__ == "__main__":
    unittest.main()
