from datetime import datetime, timezone
from datetime import date
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.crypto import decrypt_text, encrypt_text
from app.core.exceptions import BadRequestError, NotFoundError
from app.models.daily_report import DailyReport
from app.models.enums import ChannelType, NotificationStatus, SlackPurpose
from app.models.notification_log import NotificationLog
from app.models.post import Post
from app.models.slack_webhook import SlackWebhook
from app.schemas.slack import SlackTestResponse, SlackWebhookCreate, SlackWebhookRead, SlackWebhookUpdate

_IMPORTANCE_LABEL = {"high": "즉시", "medium": "참고", "low": "여유"}


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
        ok, err = self._post_message(webhook, {"text": message})
        self._log(organization_id, webhook.channel_name, message, ok, err)
        return SlackTestResponse(success=ok, message=err or "Sent successfully")

    def send_no_changes(self, organization_id: UUID, report_date: date) -> SlackTestResponse:
        webhook = self._active_webhook(organization_id, SlackPurpose.daily)
        payload = self._format_no_changes(report_date)
        ok, err = self._post_message(webhook, payload)
        self._log(organization_id, webhook.channel_name, payload["text"], ok, err)
        return SlackTestResponse(success=ok, message=err or "Sent successfully")

    def send_report(self, report_id: UUID, organization_id: UUID) -> SlackTestResponse:
        report = self.db.get(DailyReport, report_id)
        if not report or report.organization_id != organization_id:
            raise NotFoundError("Report not found")
        webhook = self._active_webhook(organization_id, SlackPurpose.daily)
        payload = self._format_report(report)
        ok, err = self._post_message(webhook, payload)
        self._log(organization_id, webhook.channel_name, payload["text"], ok, err, report_id=report.id)
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

    def _post_message(self, webhook: SlackWebhook, payload: dict) -> tuple[bool, str | None]:
        url = decrypt_text(webhook.webhook_url_encrypted)
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(url, json=payload)
            if resp.status_code >= 400:
                return False, resp.text
            return True, None
        except Exception as exc:
            return False, str(exc)

    def _mint_base_url(self) -> str | None:
        return get_settings().public_frontend_url

    def _post_url(self, post_id: str) -> str | None:
        base = self._mint_base_url()
        if not base:
            return None
        return f"{base}/posts/{post_id}"

    def _report_url(self, report_id: UUID) -> str | None:
        base = self._mint_base_url()
        if not base:
            return None
        return f"{base}/reports/{report_id}"

    @staticmethod
    def _usable_original_url(url: str | None) -> str | None:
        if not url or not url.strip():
            return None
        cleaned = url.strip()
        lowered = cleaned.lower()
        if not lowered.startswith(("http://", "https://")):
            return None
        if Settings._is_local_url(cleaned):
            return None
        return cleaned

    def _recommendation_link(self, post: Post | None, post_id: str) -> tuple[str | None, str]:
        original = self._usable_original_url(post.original_url if post else None)
        if original:
            return original, "원문 보기"
        mint_url = self._post_url(post_id)
        if mint_url:
            return mint_url, "MINT에서 보기"
        return None, ""

    def _load_posts_for_report(self, report: DailyReport) -> dict[str, Post]:
        post_ids: set[UUID] = set()
        for rec in report.key_changes or []:
            for pid in rec.get("related_post_ids") or []:
                try:
                    post_ids.add(UUID(str(pid)))
                except (TypeError, ValueError):
                    continue
        posts: dict[str, Post] = {}
        for pid in post_ids:
            post = self.db.get(Post, pid)
            if post:
                posts[str(pid)] = post
        return posts

    def _format_no_changes(self, report_date: date) -> dict:
        text = f"MINT 데일리 브리핑 ({report_date.isoformat()}) — 오늘은 새로운 변화가 없습니다."
        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "🌿 MINT 데일리 브리핑", "emoji": True},
            },
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"📅 *{report_date.isoformat()}*"}],
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "모니터링 대상 소스를 확인했으나, *오늘은 새로운 변화가 없습니다.*",
                },
            },
        ]
        return {"text": text, "blocks": blocks}

    def _format_report(self, report: DailyReport) -> dict:
        posts_by_id = self._load_posts_for_report(report)
        recs = report.key_changes or []
        fallback_lines = [report.title, report.summary or ""]
        blocks: list[dict] = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "🌿 MINT 데일리 브리핑", "emoji": True},
            },
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"📅 *{report.report_date.isoformat()}*"}],
            },
        ]

        if report.summary:
            blocks.append({"type": "divider"})
            blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": f"📌 *한눈에*\n{report.summary}"},
                }
            )

        if recs:
            blocks.append({"type": "divider"})
            blocks.append(
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "📰 *오늘 보면 좋은 소식*"},
                }
            )
            fallback_lines.append("")
            fallback_lines.append("오늘 보면 좋은 소식:")

            for i, rec in enumerate(recs[:8], 1):
                imp = rec.get("importance") or "medium"
                imp_label = _IMPORTANCE_LABEL.get(imp, "•")
                title = (rec.get("title") or "제목 없음").strip()
                why = (rec.get("description") or "").strip()
                related = rec.get("related_post_ids") or []
                link_url: str | None = None
                link_label = "원문 보기"
                for pid in related:
                    pid_str = str(pid)
                    post = posts_by_id.get(pid_str)
                    url, label = self._recommendation_link(post, pid_str)
                    if url:
                        link_url = url
                        link_label = label
                        break

                line = f"*{i}. {imp_label}* {title}"
                if why:
                    line += f"\n>{why}"
                fallback_lines.append(f"{i}. [{imp}] {title}" + (f" — {why}" if why else ""))

                section: dict = {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": line},
                }
                if link_url:
                    section["accessory"] = {
                        "type": "button",
                        "text": {"type": "plain_text", "text": link_label, "emoji": True},
                        "url": link_url,
                        "action_id": f"report_link_{i}",
                    }
                blocks.append(section)

        report_url = self._report_url(report.id)
        if report_url:
            blocks.append({"type": "divider"})
            blocks.append(
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": f"<{report_url}|MINT에서 전체 리포트 보기>",
                        }
                    ],
                }
            )

        return {"text": "\n".join(fallback_lines), "blocks": blocks}

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
