from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.exceptions import ForbiddenError, NotFoundError
from app.models.enums import InquiryStatus, UserRole
from app.models.inquiry import Inquiry, InquiryMessage
from app.models.user import User
from app.schemas.inquiry import InquiryCreate, InquiryDetail, InquiryMessageCreate, InquiryRead


class InquiryService:
    def __init__(self, db: Session):
        self.db = db

    def _base_query(self):
        return select(Inquiry).options(
            joinedload(Inquiry.user),
            joinedload(Inquiry.messages).joinedload(InquiryMessage.author),
        )

    def _get_inquiry(self, inquiry_id: UUID, organization_id: UUID) -> Inquiry:
        inquiry = self.db.scalar(
            self._base_query().where(
                Inquiry.id == inquiry_id,
                Inquiry.organization_id == organization_id,
            )
        )
        if not inquiry:
            raise NotFoundError("Inquiry not found")
        return inquiry

    def _ensure_access(self, inquiry: Inquiry, user: User) -> None:
        if user.role == UserRole.admin:
            return
        if inquiry.user_id != user.id:
            raise ForbiddenError("Access denied")

    def create_inquiry(self, organization_id: UUID, user: User, data: InquiryCreate) -> InquiryDetail:
        inquiry = Inquiry(
            organization_id=organization_id,
            user_id=user.id,
            title=data.title,
            status=InquiryStatus.open,
        )
        self.db.add(inquiry)
        self.db.flush()
        message = InquiryMessage(inquiry_id=inquiry.id, author_id=user.id, body=data.body)
        self.db.add(message)
        self.db.commit()
        return InquiryDetail.model_validate(self._get_inquiry(inquiry.id, organization_id))

    def list_mine(self, organization_id: UUID, user: User) -> list[InquiryRead]:
        rows = self.db.scalars(
            select(Inquiry)
            .options(joinedload(Inquiry.user))
            .where(Inquiry.organization_id == organization_id, Inquiry.user_id == user.id)
            .order_by(Inquiry.updated_at.desc())
        ).unique().all()
        return [InquiryRead.model_validate(r) for r in rows]

    def list_all(self, organization_id: UUID, *, status: InquiryStatus | None = None) -> list[InquiryRead]:
        stmt = (
            select(Inquiry)
            .options(joinedload(Inquiry.user))
            .where(Inquiry.organization_id == organization_id)
            .order_by(Inquiry.updated_at.desc())
        )
        if status is not None:
            stmt = stmt.where(Inquiry.status == status)
        rows = self.db.scalars(stmt).unique().all()
        return [InquiryRead.model_validate(r) for r in rows]

    def get_detail(self, inquiry_id: UUID, organization_id: UUID, user: User) -> InquiryDetail:
        inquiry = self._get_inquiry(inquiry_id, organization_id)
        self._ensure_access(inquiry, user)
        return InquiryDetail.model_validate(inquiry)

    def add_message(
        self,
        inquiry_id: UUID,
        organization_id: UUID,
        user: User,
        data: InquiryMessageCreate,
    ) -> InquiryDetail:
        inquiry = self._get_inquiry(inquiry_id, organization_id)
        self._ensure_access(inquiry, user)
        if inquiry.status == InquiryStatus.closed:
            raise ForbiddenError("Inquiry is closed")

        is_admin = user.role == UserRole.admin
        if is_admin:
            inquiry.status = InquiryStatus.answered
        elif inquiry.user_id != user.id:
            raise ForbiddenError("Access denied")

        message = InquiryMessage(inquiry_id=inquiry.id, author_id=user.id, body=data.body)
        self.db.add(message)
        self.db.commit()
        return InquiryDetail.model_validate(self._get_inquiry(inquiry_id, organization_id))

    def close(self, inquiry_id: UUID, organization_id: UUID) -> InquiryDetail:
        inquiry = self._get_inquiry(inquiry_id, organization_id)
        inquiry.status = InquiryStatus.closed
        self.db.commit()
        return InquiryDetail.model_validate(self._get_inquiry(inquiry_id, organization_id))

    def count_open(self, organization_id: UUID) -> int:
        from sqlalchemy import func

        return self.db.scalar(
            select(func.count())
            .select_from(Inquiry)
            .where(
                Inquiry.organization_id == organization_id,
                Inquiry.status == InquiryStatus.open,
            )
        ) or 0
