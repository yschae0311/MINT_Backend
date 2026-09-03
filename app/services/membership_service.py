from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.models.edition import Edition, SourceEdition, UserEdition
from app.models.enums import KeywordScope, KeywordStatus, UserRole
from app.models.personalization import Keyword, NewsCategory, PostKeyword
from app.models.post import Post
from app.models.source import Source
from app.models.user import User
from app.schemas.auth import UserEditionMembership, UserRead
from app.schemas.user import UserAdminRead, UserEditionAssignment


def is_org_admin(user: User) -> bool:
    return user.role == UserRole.admin


class MembershipService:
    def __init__(self, db: Session):
        self.db = db

    def membership_rows(self, user_id: UUID) -> list[UserEdition]:
        return list(
            self.db.scalars(select(UserEdition).where(UserEdition.user_id == user_id)).all()
        )

    def membership_reads(self, user: User) -> list[UserEditionMembership]:
        rows = self.db.execute(
            select(UserEdition, Edition)
            .join(Edition, Edition.id == UserEdition.edition_id)
            .where(UserEdition.user_id == user.id)
            .order_by(Edition.sort_order, Edition.name)
        ).all()
        return [
            UserEditionMembership(
                id=edition.id,
                name=edition.name,
                slug=edition.slug,
                is_editor=membership.is_editor,
            )
            for membership, edition in rows
        ]

    def to_user_read(self, user: User) -> UserRead:
        data = UserRead.model_validate(user)
        data.editions = self.membership_reads(user)
        return data

    def to_admin_read(self, user: User) -> UserAdminRead:
        data = UserAdminRead.model_validate(user)
        data.editions = self.membership_reads(user)
        return data

    def visible_edition_ids(self, user: User, *, active_only: bool = True) -> set[UUID] | None:
        """None = unrestricted (org super-admin). Empty set = no assigned editions."""
        if is_org_admin(user):
            return None
        q = (
            select(UserEdition.edition_id)
            .join(Edition, Edition.id == UserEdition.edition_id)
            .where(
                UserEdition.user_id == user.id,
                Edition.organization_id == user.organization_id,
            )
        )
        if active_only:
            q = q.where(Edition.is_active.is_(True))
        return set(self.db.scalars(q).all())

    def editor_edition_ids(self, user: User) -> set[UUID]:
        if is_org_admin(user):
            return set(
                self.db.scalars(
                    select(Edition.id).where(
                        Edition.organization_id == user.organization_id,
                        Edition.is_active.is_(True),
                    )
                ).all()
            )
        return set(
            self.db.scalars(
                select(UserEdition.edition_id)
                .join(Edition, Edition.id == UserEdition.edition_id)
                .where(
                    UserEdition.user_id == user.id,
                    UserEdition.is_editor.is_(True),
                    Edition.organization_id == user.organization_id,
                )
            ).all()
        )

    def can_edit_any(self, user: User) -> bool:
        if is_org_admin(user):
            return True
        return bool(
            self.db.scalar(
                select(UserEdition.id).where(
                    UserEdition.user_id == user.id,
                    UserEdition.is_editor.is_(True),
                )
            )
        )

    def can_view_edition(self, user: User, edition_id: UUID) -> bool:
        visible = self.visible_edition_ids(user)
        if visible is None:
            return True
        return edition_id in visible

    def can_edit_edition(self, user: User, edition_id: UUID) -> bool:
        if is_org_admin(user):
            return True
        return edition_id in self.editor_edition_ids(user)

    def assert_view(self, user: User, edition_id: UUID) -> None:
        row = self.db.get(Edition, edition_id)
        if not row or row.organization_id != user.organization_id:
            raise NotFoundError("Edition not found")
        if not self.can_view_edition(user, edition_id):
            raise NotFoundError("Edition not found")

    def assert_editor(self, user: User, edition_id: UUID) -> None:
        self.assert_view(user, edition_id)
        if not self.can_edit_edition(user, edition_id):
            raise ForbiddenError("이 분야의 편집 권한이 없습니다.")

    def assert_editor_any(self, user: User) -> None:
        if not self.can_edit_any(user):
            raise ForbiddenError("분야 편집 권한이 없습니다.")

    def filter_editions(self, user: User, rows: list[Edition]) -> list[Edition]:
        visible = self.visible_edition_ids(user, active_only=False)
        if visible is None:
            return rows
        return [row for row in rows if row.id in visible]

    def visible_keyword_ids(self, user: User) -> set[UUID] | None:
        """None = all org keywords. Empty = none."""
        visible = self.visible_edition_ids(user)
        if visible is None:
            return None
        if not visible:
            return set()
        return set(
            self.db.scalars(
                select(Keyword.id).where(
                    Keyword.organization_id == user.organization_id,
                    Keyword.scope == KeywordScope.organization,
                    Keyword.status.in_([KeywordStatus.active, KeywordStatus.candidate]),
                    Keyword.edition_id.in_(visible),
                )
            ).all()
        )

    def restrict_keyword_ids(self, user: User, keyword_ids: list[UUID] | None) -> set[UUID]:
        requested = set(keyword_ids or [])
        allowed = self.visible_keyword_ids(user)
        if allowed is None:
            return requested
        if requested:
            return requested & allowed
        return allowed

    def keyword_in_scope(self, user: User, keyword: Keyword) -> bool:
        visible = self.visible_edition_ids(user)
        if visible is None:
            return True
        if keyword.edition_id is None:
            return bool(visible)
        return keyword.edition_id in visible

    def category_in_scope(self, user: User, category: NewsCategory) -> bool:
        visible = self.visible_edition_ids(user)
        if visible is None:
            return True
        if category.edition_id is None:
            return bool(visible)
        return category.edition_id in visible

    def source_visible(self, user: User, source: Source) -> bool:
        visible = self.visible_edition_ids(user)
        if visible is None:
            return True
        tags = set(
            self.db.scalars(
                select(SourceEdition.edition_id).where(SourceEdition.source_id == source.id)
            ).all()
        )
        if not tags:
            return True
        return bool(tags & visible)

    def assert_source_visible(self, user: User, source: Source) -> None:
        if source.organization_id != user.organization_id:
            raise NotFoundError("Source not found")
        if not self.source_visible(user, source):
            raise NotFoundError("Source not found")

    def assert_source_editable(self, user: User, source: Source) -> None:
        self.assert_source_visible(user, source)
        if is_org_admin(user):
            return
        editor_ids = self.editor_edition_ids(user)
        if not editor_ids:
            raise ForbiddenError("분야 편집 권한이 없습니다.")
        tags = set(
            self.db.scalars(
                select(SourceEdition.edition_id).where(SourceEdition.source_id == source.id)
            ).all()
        )
        if tags and not (tags & editor_ids):
            raise ForbiddenError("이 소스의 편집 권한이 없습니다.")

    def constrain_source_edition_ids(self, user: User, edition_ids: list[UUID] | None) -> list[UUID] | None:
        if edition_ids is None:
            return None
        if is_org_admin(user):
            return edition_ids
        allowed = self.editor_edition_ids(user)
        unique = list(dict.fromkeys(edition_ids))
        if any(edition_id not in allowed for edition_id in unique):
            raise ForbiddenError("담당 분야가 아닌 태그는 지정할 수 없습니다.")
        return unique

    def review_item_visible(self, user: User, post: Post) -> bool:
        visible = self.visible_edition_ids(user)
        if visible is None:
            return True
        if not visible:
            return False
        tagged = bool(
            self.db.scalar(
                select(PostKeyword.id)
                .join(Keyword, Keyword.id == PostKeyword.keyword_id)
                .where(
                    PostKeyword.post_id == post.id,
                    Keyword.edition_id.in_(visible),
                )
                .limit(1)
            )
        )
        if tagged:
            return True
        if not post.source_id:
            return False
        source_tags = set(
            self.db.scalars(
                select(SourceEdition.edition_id).where(SourceEdition.source_id == post.source_id)
            ).all()
        )
        if not source_tags:
            return True
        return bool(source_tags & visible)

    def set_user_editions(
        self,
        target: User,
        assignments: list[UserEditionAssignment],
    ) -> list[UserEditionMembership]:
        unique: dict[UUID, UserEditionAssignment] = {}
        for item in assignments:
            unique[item.edition_id] = item
        edition_ids = list(unique.keys())
        editions = list(
            self.db.scalars(
                select(Edition).where(
                    Edition.organization_id == target.organization_id,
                    Edition.id.in_(edition_ids) if edition_ids else Edition.id.is_(None),
                )
            ).all()
        ) if edition_ids else []
        if len(editions) != len(edition_ids):
            raise BadRequestError("선택할 수 없는 분야가 포함되어 있습니다.")

        self.db.execute(delete(UserEdition).where(UserEdition.user_id == target.id))
        for item in unique.values():
            self.db.add(
                UserEdition(
                    user_id=target.id,
                    edition_id=item.edition_id,
                    is_editor=item.is_editor,
                )
            )
        self.db.commit()
        return self.membership_reads(target)
