from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.permissions import require_admin
from app.core.security import get_current_user
from app.models.user import User
from app.schemas.slack import (
    SlackTestRequest,
    SlackTestResponse,
    SlackWebhookCreate,
    SlackWebhookRead,
    SlackWebhookUpdate,
)
from app.services.slack_service import SlackService

router = APIRouter()


@router.get("/webhooks", response_model=list[SlackWebhookRead])
def list_webhooks(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    return SlackService(db).list_webhooks(user.organization_id)


@router.post("/webhooks", response_model=SlackWebhookRead)
def create_webhook(
    data: SlackWebhookCreate,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return SlackService(db).create_webhook(user.organization_id, data)


@router.patch("/webhooks/{webhook_id}", response_model=SlackWebhookRead)
def update_webhook(
    webhook_id: UUID,
    data: SlackWebhookUpdate,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return SlackService(db).update_webhook(webhook_id, user.organization_id, data)


@router.delete("/webhooks/{webhook_id}", status_code=204)
def delete_webhook(
    webhook_id: UUID,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    SlackService(db).delete_webhook(webhook_id, user.organization_id)


@router.post("/test", response_model=SlackTestResponse)
def test_slack(
    data: SlackTestRequest,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return SlackService(db).send_test(user.organization_id, data.message)
