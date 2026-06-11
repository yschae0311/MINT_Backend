from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.core.exceptions import BadRequestError, NotFoundError
from app.models.daily_report import DailyReport, DailyReportItem
from app.models.enums import BoardType, Importance, PostStatus
from app.models.post import Post
from app.schemas.post import PostRead
from app.schemas.report import DailyReportDetail, DailyReportItemRead, DailyReportRead
from app.services.llm_client import get_llm_client

KST = ZoneInfo("Asia/Seoul")


class ReportService:
    def __init__(self, db: Session):
        self.db = db

    def list_reports(self, organization_id: UUID) -> list[DailyReportRead]:
        reports = self.db.scalars(
            select(DailyReport)
            .where(DailyReport.organization_id == organization_id)
            .order_by(DailyReport.report_date.desc())
        ).all()
        return [DailyReportRead.model_validate(r) for r in reports]

    def delete_report(self, report_id: UUID, organization_id: UUID) -> None:
        report = self.db.scalar(
            select(DailyReport).where(
                DailyReport.id == report_id,
                DailyReport.organization_id == organization_id,
            )
        )
        if not report:
            raise NotFoundError("Report not found")
        self.db.delete(report)
        self.db.commit()

    def get_report(self, report_id: UUID, organization_id: UUID) -> DailyReportDetail:
        report = self.db.scalar(
            select(DailyReport)
            .options(joinedload(DailyReport.items))
            .where(DailyReport.id == report_id, DailyReport.organization_id == organization_id)
        )
        if not report:
            raise NotFoundError("Report not found")

        detail = DailyReportDetail.model_validate(report)
        items = []
        for item in report.items:
            post = self.db.get(Post, item.post_id)
            row = DailyReportItemRead.model_validate(item)
            if post:
                row.post = PostRead.model_validate(post)
                row.post.source_name = post.source.name if post.source else None
            items.append(row)
        detail.items = items
        return detail

    def _resolve_report_date(self, report_date: date | None, *, prefer_yesterday: bool) -> date:
        if report_date is not None:
            return report_date
        today_kst = datetime.now(KST).date()
        if prefer_yesterday:
            return today_kst - timedelta(days=1)
        return today_kst

    def _day_range(self, target: date) -> tuple[datetime, datetime]:
        start = datetime.combine(target, datetime.min.time(), tzinfo=KST)
        return start, start + timedelta(days=1)

    def _post_summary_for_report(self, post: Post) -> str:
        if post.raw_content and post.raw_content.strip():
            return post.raw_content.strip()[:500]
        if post.ai_outputs:
            latest = max(post.ai_outputs, key=lambda o: o.created_at)
            if latest.summary:
                return latest.summary[:500]
        return post.title

    def generate(
        self,
        organization_id: UUID,
        report_date: date | None = None,
        *,
        prefer_yesterday: bool = False,
        allow_empty: bool = False,
    ) -> DailyReportDetail:
        target = self._resolve_report_date(report_date, prefer_yesterday=prefer_yesterday)
        start, end = self._day_range(target)

        posts = self.db.scalars(
            select(Post)
            .options(joinedload(Post.source), joinedload(Post.ai_outputs))
            .where(
                Post.organization_id == organization_id,
                Post.collected_at >= start,
                Post.collected_at < end,
                Post.status.in_([PostStatus.published, PostStatus.pending]),
            )
        ).unique().all()

        eligible = [
            p
            for p in posts
            if p.board_type in (BoardType.trusted, BoardType.discovery)
        ]
        trusted_posts = [p for p in eligible if p.board_type == BoardType.trusted]
        discovery_posts = [p for p in eligible if p.board_type == BoardType.discovery]

        if not eligible:
            if not allow_empty:
                raise BadRequestError(
                    f"{target.isoformat()} 기준으로 리포트에 포함할 게시글이 없습니다. "
                    "해당 날짜에 수집된 중요/AI 발견 게시글이 있는지 확인하세요."
                )
            return self._create_empty_report(organization_id, target)

        # 중요 게시판 우선, AI 발견 게시판을 함께 포함 (최대 40건)
        ordered = trusted_posts + discovery_posts
        payload = [
            {
                "id": str(p.id),
                "title": p.title,
                "summary": self._post_summary_for_report(p),
                "board": "trusted" if p.board_type == BoardType.trusted else "discovery",
                "importance": p.importance.value if p.importance != Importance.unknown else "medium",
            }
            for p in ordered[:40]
        ]
        client = get_llm_client()
        result = client.generate_daily_report(payload)
        normalized = self._normalize_report_result(result, target)

        report = DailyReport(
            organization_id=organization_id,
            report_date=target,
            title=normalized["title"],
            summary=normalized["summary"],
            key_changes=normalized["key_changes"],
            risks=normalized.get("risks"),
            action_items=normalized.get("action_items"),
            model=getattr(client, "report_model", "mock"),
            prompt_version="v2",
        )
        self.db.add(report)
        self.db.flush()

        posts_by_id = {str(p.id): p for p in ordered}
        related_ids: list[str] = []
        rec_by_post: dict[str, str] = {}
        for change in normalized["key_changes"]:
            why = (change.get("description") or "").strip()
            for pid in change.get("related_post_ids") or []:
                pid_str = str(pid)
                if pid_str not in related_ids:
                    related_ids.append(pid_str)
                if why and pid_str not in rec_by_post:
                    rec_by_post[pid_str] = why

        item_posts: list[Post] = []
        for pid in related_ids:
            post = posts_by_id.get(pid)
            if post and post not in item_posts:
                item_posts.append(post)
        for post in ordered:
            if post.importance == Importance.high and post not in item_posts:
                item_posts.append(post)
        if not item_posts:
            item_posts = ordered[:10]

        for post in item_posts[:20]:
            board_label = "중요" if post.board_type == BoardType.trusted else "AI발견"
            why = rec_by_post.get(str(post.id))
            reason = f"[{board_label}] {why}" if why else f"[{board_label}] {post.title[:120]}"
            self.db.add(
                DailyReportItem(
                    report_id=report.id,
                    post_id=post.id,
                    reason=reason[:200],
                    importance=post.importance if post.importance != Importance.unknown else Importance.medium,
                )
            )

        self.db.commit()
        return self.get_report(report.id, organization_id)

    def _normalize_report_result(self, result: dict, target: date) -> dict:
        recs = result.get("recommendations") or result.get("key_changes") or []
        key_changes = []
        for rec in recs[:8]:
            why = (rec.get("why_read") or rec.get("description") or "").strip()
            key_changes.append(
                {
                    "title": (rec.get("title") or "").strip()[:120],
                    "description": why[:200],
                    "related_post_ids": rec.get("related_post_ids") or [],
                    "importance": rec.get("importance") or "medium",
                }
            )

        risks = result.get("risks")
        if isinstance(risks, list):
            risks = [r.strip() for r in risks if isinstance(r, str) and r.strip()][:2] or None
        else:
            risks = None

        action_items = result.get("action_items")
        if isinstance(action_items, list):
            action_items = [a.strip() for a in action_items if isinstance(a, str) and a.strip()][:2] or None
        else:
            action_items = None

        title = (result.get("title") or f"MINT 브리핑 · {target.isoformat()}").strip()
        summary = (result.get("summary") or "").strip()[:400]
        return {
            "title": title,
            "summary": summary,
            "key_changes": key_changes,
            "risks": risks,
            "action_items": action_items,
        }

    def _create_empty_report(self, organization_id: UUID, target: date) -> DailyReportDetail:
        report = DailyReport(
            organization_id=organization_id,
            report_date=target,
            title=f"MINT 일일 리포트 ({target.isoformat()})",
            summary="모니터링 대상 소스를 확인했으나, 오늘은 새로운 변화가 없습니다.",
            model="none",
            prompt_version="v1",
        )
        self.db.add(report)
        self.db.commit()
        return self.get_report(report.id, organization_id)
