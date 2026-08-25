import os
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.core.database import Base
import app.models  # noqa: F401
from app.models.enums import AccountApprovalStatus, UserRole
from app.models.organization import Organization
from app.models.user import User
from app.services.keycloak_service import KeycloakAuthService, extract_roles


class KeycloakLoginTest(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["KEYCLOAK_ISSUER"] = "https://keycloak.example.com/realms/motrex"
        os.environ["KEYCLOAK_CLIENT_ID"] = "mint"
        os.environ["KEYCLOAK_ADMIN_ROLE"] = "mint-superadmin"
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
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()
        for key in ("KEYCLOAK_ISSUER", "KEYCLOAK_CLIENT_ID", "KEYCLOAK_ADMIN_ROLE"):
            os.environ.pop(key, None)
        get_settings.cache_clear()

    def test_extract_roles_from_realm_and_resource(self) -> None:
        roles = extract_roles(
            {
                "realm_access": {"roles": ["mint-superadmin", "offline_access"]},
                "resource_access": {"mint": {"roles": ["user"]}},
            }
        )
        self.assertIn("mint-superadmin", roles)
        self.assertIn("user", roles)

    def test_first_login_inserts_user_from_userinfo(self) -> None:
        claims = {
            "sub": "kc-1",
            "iss": "https://keycloak.example.com/realms/motrex",
            "azp": "mint",
        }
        with (
            patch("app.services.keycloak_service.verify_access_token", return_value=claims),
            patch(
                "app.services.keycloak_service.fetch_userinfo",
                return_value={"sub": "kc-1", "email": "new@example.com", "name": "신규"},
            ),
        ):
            KeycloakAuthService(self.db).login(access_token="fake")
        user = self.db.scalar(select(User).where(User.keycloak_sub == "kc-1"))
        self.assertIsNotNone(user)
        assert user is not None
        self.assertEqual(user.email, "new@example.com")
        self.assertEqual(user.name, "신규")
        self.assertEqual(user.role, UserRole.viewer)
        self.assertEqual(user.approval_status, AccountApprovalStatus.approved)
        self.assertTrue(user.is_active)

    def test_second_login_updates_profile_and_admin_role(self) -> None:
        org = self.db.scalar(select(Organization))
        assert org is not None
        existing = User(
            organization_id=org.id,
            email="old@example.com",
            password_hash="x",
            name="이전",
            keycloak_sub="kc-2",
            role=UserRole.admin,
            approval_status=AccountApprovalStatus.approved,
            is_active=True,
        )
        self.db.add(existing)
        self.db.commit()
        claims = {
            "sub": "kc-2",
            "email": "newmail@example.com",
            "name": "갱신",
            "azp": "mint",
            "realm_access": {"roles": []},
        }
        with patch("app.services.keycloak_service.verify_access_token", return_value=claims):
            KeycloakAuthService(self.db).login(access_token="fake")
        self.db.refresh(existing)
        self.assertEqual(existing.email, "newmail@example.com")
        self.assertEqual(existing.name, "갱신")
        self.assertEqual(existing.role, UserRole.viewer)

    def test_links_seed_admin_by_email(self) -> None:
        org = self.db.scalar(select(Organization))
        assert org is not None
        seed = User(
            organization_id=org.id,
            email="admin@motrexev.com",
            password_hash="x",
            name="김민트",
            role=UserRole.admin,
            approval_status=AccountApprovalStatus.approved,
            is_active=True,
        )
        self.db.add(seed)
        self.db.commit()
        claims = {"sub": "kc-admin", "azp": "mint", "realm_access": {"roles": ["mint-superadmin"]}}
        with (
            patch("app.services.keycloak_service.verify_access_token", return_value=claims),
            patch(
                "app.services.keycloak_service.fetch_userinfo",
                return_value={
                    "sub": "kc-admin",
                    "email": "admin@motrexev.com",
                    "name": "김민트",
                },
            ),
        ):
            KeycloakAuthService(self.db).login(access_token="fake")
        self.db.refresh(seed)
        self.assertEqual(seed.keycloak_sub, "kc-admin")
        self.assertEqual(seed.role, UserRole.admin)


if __name__ == "__main__":
    unittest.main()
