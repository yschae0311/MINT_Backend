from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.enums import InquiryStatus
from app.schemas.common import ORMBase


class InquiryCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    body: str = Field(min_length=1, max_length=10000)


class InquiryMessageCreate(BaseModel):
    body: str = Field(min_length=1, max_length=10000)


class InquiryAuthorRead(ORMBase):
    id: UUID
    name: str
    email: str
    role: str


class InquiryMessageRead(ORMBase):
    id: UUID
    inquiry_id: UUID
    author_id: UUID
    body: str
    created_at: datetime
    author: InquiryAuthorRead


class InquiryRead(ORMBase):
    id: UUID
    organization_id: UUID
    user_id: UUID
    title: str
    status: InquiryStatus
    created_at: datetime
    updated_at: datetime
    user: InquiryAuthorRead


class InquiryDetail(InquiryRead):
    messages: list[InquiryMessageRead]
