from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from app.models.enums import SlackPurpose
from app.schemas.common import ORMBase


class SlackWebhookCreate(BaseModel):
    webhook_url: str
    channel_name: str = "#ev-intel"
    purpose: SlackPurpose = SlackPurpose.all
    is_active: bool = True


class SlackWebhookUpdate(BaseModel):
    webhook_url: str | None = None
    channel_name: str | None = None
    purpose: SlackPurpose | None = None
    is_active: bool | None = None


class SlackWebhookRead(ORMBase):
    id: UUID
    organization_id: UUID
    channel_name: str
    purpose: SlackPurpose
    is_active: bool
    created_at: datetime
    updated_at: datetime


class SlackTestRequest(BaseModel):
    message: str = "MINT 테스트 메시지입니다."


class SlackTestResponse(BaseModel):
    success: bool
    message: str
