from datetime import datetime, timezone
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import decrypt_text, encrypt_text
from app.core.exceptions import BadRequestError, NotFoundError
from app.models.daily_report import DailyReport
from app.models.enums import ChannelType, NotificationStatus, SlackPurpose
from app.models.notification_log import NotificationLog
from app.models.slack_webhook import SlackWebhook
from app.schemas.slack import SlackTestResponse, SlackWebhookCreate, SlackWebhookRead, SlackWebhookUpdate


class SlackService:
    def __init__(self, db: Session):
        self.db = db

    def list_webhooks(self, organization_id: UUID) -> list[SlackWebhookRead]:
        rows = self.db.scalars(
            select(SlackWebhook).where(SlackWebhook.organization_id == organization_id)
        ).all()
        return [SlackWebhookRead.model_validate(r) for r in rows]

    def create_webhook(self, organization_id: UUID, data: SlackWebhookCreate) -> SlackWebhookRead:
        row = SlackWebhook(
            organization_id=organization_id,
            webhook_url_encrypted=encrypt_text(data.webhook_url),
            channel_name=data.channel_name,
            purpose=data.purpose,
            is_active=data.is_active,
        )
        self.db.add(row)
        self.db.commit()
        self.db.refresh(row)
        return SlackWebhookRead.model_validate(row)

    def update_webhook(
        self, webhook_id: UUID, organization_id: UUID, data: SlackWebhookUpdate
    ) -> SlackWebhookRead:
        row = self._get_or_404(webhook_id, organization_id)
        if data.webhook_url is not None:
            row.webhook_url_encrypted = encrypt_text(data.webhook_url)
        if data.channel_name is not None:
            row.channel_name = data.channel_name
        if data.purpose is not None:
            row.purpose = data.purpose
        if data.is_active is not None:
            row.is_active = data.is_active
        self.db.commit()
        self.db.refresh(row)
        return SlackWebhookRead.model_validate(row)

    def delete_webhook(self, webhook_id: UUID, organization_id: UUID) -> None:
        row = self._get_or_404(webhook_id, organization_id)
        self.db.delete(row)
        self.db.commit()

    def send_test(self, organization_id: UUID, message: str) -> SlackTestResponse:
        webhook = self._active_webhook(organization_id, SlackPurpose.all)
        ok, err = self._post_message(webhook, message)
        self._log(organization_id, webhook.channel_name, message, ok, err)
        return SlackTestResponse(success=ok, message=err or "Sent successfully")

    def send_report(self, report_id: UUID, organization_id: UUID) -> SlackTestResponse:
        report = self.db.get(DailyReport, report_id)
        if not report or report.organization_id != organization_id:
            raise NotFoundError("Report not found")
        webhook = self._active_webhook(organization_id, SlackPurpose.daily)
        text = self._format_report(report)
        ok, err = self._post_message(webhook, text)
        self._log(organization_id, webhook.channel_name, text, ok, err, report_id=report.id)
        if ok:
            report.slack_sent = True
            self.db.commit()
        return SlackTestResponse(success=ok, message=err or "Report sent")

    def _active_webhook(self, organization_id: UUID, purpose: SlackPurpose) -> SlackWebhook:
        webhooks = self.db.scalars(
            select(SlackWebhook).where(
                SlackWebhook.organization_id == organization_id,
                SlackWebhook.is_active.is_(True),
            )
        ).all()
        for w in webhooks:
            if w.purpose in (purpose, SlackPurpose.all):
                return w
        if webhooks:
            return webhooks[0]
        raise BadRequestError("No active Slack webhook configured")

    def _post_message(self, webhook: SlackWebhook, text: str) -> tuple[bool, str | None]:
        url = decrypt_text(webhook.webhook_url_encrypted)
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(url, json={"text": text})
            if resp.status_code >= 400:
                return False, resp.text
            return True, None
        except Exception as exc:
            return False, str(exc)

    def _format_report(self, report: DailyReport) -> str:
        lines = [
            f"*{report.title}*",
            report.summary,
            "",
            f"Report date: {report.report_date}",
        ]
        if report.action_items:
            lines.append("\n*Action items:*")
            for item in report.action_items:
                lines.append(f"• {item}")
        return "\n".join(lines)

    def _log(
        self,
        organization_id: UUID,
        channel: str,
        message: str,
        ok: bool,
        err: str | None,
        report_id: UUID | None = None,
    ) -> None:
        log = NotificationLog(
            organization_id=organization_id,
            channel_type=ChannelType.slack,
            channel_name=channel,
            report_id=report_id,
            message=message[:4000],
            status=NotificationStatus.success if ok else NotificationStatus.failed,
            sent_at=datetime.now(timezone.utc) if ok else None,
            error_message=err,
        )
        self.db.add(log)
        self.db.commit()

    def _get_or_404(self, webhook_id: UUID, organization_id: UUID) -> SlackWebhook:
        row = self.db.get(SlackWebhook, webhook_id)
        if not row or row.organization_id != organization_id:
            raise NotFoundError("Webhook not found")
        return row
