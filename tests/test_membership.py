import os
import unittest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.core.database import Base
import app.models  # noqa: F401
from app.core.exceptions import ForbiddenError, NotFoundError
from app.models.edition import UserEdition
from app.models.enums import AccountApprovalStatus, UserRole
from app.models.organization import Organization
from app.models.user import User
from app.schemas.user import UserEditionAssignment
from app.services.edition_service import AUTONOMOUS_SLUG, EV_SLUG, EditionService
from app.services.membership_service import MembershipService
from app.services.personalization_service import TaxonomyService


class MembershipServiceTest(unittest.TestCase):
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
        self.member = self._user("av@example.com", UserRole.viewer, "파트")
        TaxonomyService(self.db).ensure_defaults(org.id)
        self.db.commit()
        self.editions = {row.slug: row for row in EditionService(self.db).ensure_defaults(org.id)}
        self.membership = MembershipService(self.db)

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

    def test_admin_sees_all_editions(self) -> None:
        self.assertIsNone(self.membership.visible_edition_ids(self.admin))

    def test_member_sees_assigned_only(self) -> None:
        av = self.editions[AUTONOMOUS_SLUG]
        self.membership.set_user_editions(
            self.member,
            [UserEditionAssignment(edition_id=av.id, is_editor=False)],
        )
        visible = self.membership.visible_edition_ids(self.member)
        self.assertEqual(visible, {av.id})
        self.membership.assert_view(self.member, av.id)
        with self.assertRaises(NotFoundError):
            self.membership.assert_view(self.member, self.editions[EV_SLUG].id)

    def test_multiple_editors_per_edition(self) -> None:
        from sqlalchemy import select

        av = self.editions[AUTONOMOUS_SLUG]
        other = self._user("ed2@example.com", UserRole.viewer, "두번째")
        self.membership.set_user_editions(
            self.member,
            [UserEditionAssignment(edition_id=av.id, is_editor=True)],
        )
        self.membership.set_user_editions(
            other,
            [UserEditionAssignment(edition_id=av.id, is_editor=True)],
        )
        editors = list(self.db.scalars(select(UserEdition).where(UserEdition.edition_id == av.id)).all())
        self.assertEqual(sum(1 for row in editors if row.is_editor), 2)
        self.assertTrue(any(row.user_id == other.id and row.is_editor for row in editors))
        self.assertTrue(any(row.user_id == self.member.id and row.is_editor for row in editors))

    def test_editor_cannot_edit_other_edition(self) -> None:
        av = self.editions[AUTONOMOUS_SLUG]
        ev = self.editions[EV_SLUG]
        self.membership.set_user_editions(
            self.member,
            [
                UserEditionAssignment(edition_id=av.id, is_editor=True),
                UserEditionAssignment(edition_id=ev.id, is_editor=False),
            ],
        )
        self.membership.assert_editor(self.member, av.id)
        with self.assertRaises(ForbiddenError):
            self.membership.assert_editor(self.member, ev.id)

    def test_unassigned_member_has_empty_home(self) -> None:
        self.assertEqual(self.membership.visible_edition_ids(self.member), set())
        self.assertEqual(self.membership.visible_keyword_ids(self.member), set())

    def test_user_read_includes_memberships(self) -> None:
        av = self.editions[AUTONOMOUS_SLUG]
        self.membership.set_user_editions(
            self.member,
            [UserEditionAssignment(edition_id=av.id, is_editor=True)],
        )
        read = self.membership.to_user_read(self.member)
        self.assertEqual(len(read.editions), 1)
        self.assertEqual(read.editions[0].slug, AUTONOMOUS_SLUG)
        self.assertTrue(read.editions[0].is_editor)

    def test_member_can_choose_own_editions(self) -> None:
        from sqlalchemy import select

        av = self.editions[AUTONOMOUS_SLUG]
        ev = self.editions[EV_SLUG]
        self.membership.set_my_editions(self.member, [av.id, ev.id])
        visible = self.membership.visible_edition_ids(self.member)
        self.assertEqual(visible, {av.id, ev.id})
        rows = list(self.db.scalars(select(UserEdition).where(UserEdition.user_id == self.member.id)).all())
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(not row.is_editor for row in rows))

    def test_self_serve_cannot_grant_editor(self) -> None:
        av = self.editions[AUTONOMOUS_SLUG]
        self.membership.set_my_editions(self.member, [av.id])
        read = self.membership.to_user_read(self.member)
        self.assertEqual(len(read.editions), 1)
        self.assertFalse(read.editions[0].is_editor)

    def test_self_serve_keeps_existing_editor_desk(self) -> None:
        av = self.editions[AUTONOMOUS_SLUG]
        ev = self.editions[EV_SLUG]
        self.membership.set_user_editions(
            self.member,
            [UserEditionAssignment(edition_id=av.id, is_editor=True)],
        )
        self.membership.set_my_editions(self.member, [ev.id])
        read = {item.id: item for item in self.membership.to_user_read(self.member).editions}
        self.assertTrue(read[av.id].is_editor)
        self.assertFalse(read[ev.id].is_editor)

    def test_self_serve_rejects_empty_and_unknown(self) -> None:
        from app.core.exceptions import BadRequestError
        from uuid import uuid4

        with self.assertRaises(BadRequestError):
            self.membership.set_my_editions(self.member, [])
        with self.assertRaises(BadRequestError):
            self.membership.set_my_editions(self.member, [uuid4()])
        with self.assertRaises(BadRequestError):
            self.membership.set_my_editions(self.admin, [self.editions[EV_SLUG].id])

    def test_unassigned_member_catalog_is_still_visible(self) -> None:
        self.assertEqual(self.membership.visible_edition_ids(self.member), set())
        catalog = EditionService(self.db).list_reads(self.org_id, active_only=True)
        self.assertGreaterEqual(len(catalog), 2)
        slugs = {row.slug for row in catalog}
        self.assertIn(EV_SLUG, slugs)
        self.assertIn(AUTONOMOUS_SLUG, slugs)



if __name__ == "__main__":
    unittest.main()
