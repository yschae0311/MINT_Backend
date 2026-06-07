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
            if p.board_type == BoardType.trusted
            or p.importance == Importance.high
            or (p.board_type == BoardType.discovery and p.status == PostStatus.pending)
        ]
        if not eligible:
            if not allow_empty:
                raise BadRequestError(
                    f"{target.isoformat()} 기준으로 리포트에 포함할 게시글이 없습니다. "
                    "해당 날짜에 수집된 중요/AI 발견 게시글이 있는지 확인하세요."
                )
            return self._create_empty_report(organization_id, target)

        payload = [
            {"id": str(p.id), "title": p.title, "summary": self._post_summary_for_report(p)}
            for p in eligible[:40]
        ]
        client = get_llm_client()
        result = client.generate_daily_report(payload)

        report = DailyReport(
            organization_id=organization_id,
            report_date=target,
            title=result.get("title", f"Daily Report {target}"),
            summary=result.get("summary", ""),
            key_changes=result.get("key_changes"),
            risks=result.get("risks"),
            action_items=result.get("action_items"),
            model=getattr(client, "report_model", "mock"),
            prompt_version="v1",
        )
        self.db.add(report)
        self.db.flush()

        related_ids = set()
        for change in result.get("key_changes") or []:
            for pid in change.get("related_post_ids") or []:
                related_ids.add(str(pid))

        for post in eligible:
            if str(post.id) in related_ids or post.importance == Importance.high:
                self.db.add(
                    DailyReportItem(
                        report_id=report.id,
                        post_id=post.id,
                        reason=post.title[:200],
                        importance=post.importance if post.importance != Importance.unknown else Importance.medium,
                    )
                )

        if not report.items:
            for post in eligible[:10]:
                self.db.add(
                    DailyReportItem(
                        report_id=report.id,
                        post_id=post.id,
                        reason="Included in daily report",
                        importance=post.importance if post.importance != Importance.unknown else Importance.medium,
                    )
                )

        self.db.commit()
        return self.get_report(report.id, organization_id)

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
