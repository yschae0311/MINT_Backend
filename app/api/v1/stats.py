from datetime import datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.daily_report import DailyReport
from app.models.enums import BoardType, Importance, PostStatus
from app.models.post import Post
from app.models.source import Source
from app.models.user import User
from app.schemas.stats import DashboardLatestReport, DashboardPostPreview, DashboardStatsResponse
from app.services.post_service import PostService

router = APIRouter()

KST = ZoneInfo("Asia/Seoul")


def _today_range_kst() -> tuple[datetime, datetime]:
    today = datetime.now(KST).date()
    start = datetime.combine(today, datetime.min.time(), tzinfo=KST)
    return start, start + timedelta(days=1)


def _post_preview(post: Post) -> DashboardPostPreview:
    ai_summary = None
    if post.ai_outputs:
        latest = max(post.ai_outputs, key=lambda o: o.created_at)
        ai_summary = (latest.summary or "")[:240] or None
    return DashboardPostPreview(
        id=str(post.id),
        title=post.title,
        source_name=post.source.name if post.source else None,
        board_type=post.board_type,
        status=post.status,
        importance=post.importance,
        collected_at=post.collected_at,
        original_url=post.original_url,
        ai_summary=ai_summary,
    )


def _recent_posts(
    db: Session,
    org_id: UUID,
    *,
    board_type: BoardType | None = None,
    status: PostStatus | None = None,
    limit: int = 5,
) -> list[DashboardPostPreview]:
    q = (
        select(Post)
        .options(joinedload(Post.source), joinedload(Post.ai_outputs))
        .where(Post.organization_id == org_id, Post.status != PostStatus.deleted)
    )
    if board_type:
        q = q.where(Post.board_type == board_type)
    if status:
        q = q.where(Post.status == status)
    posts = db.scalars(q.order_by(Post.collected_at.desc()).limit(limit)).unique().all()
    return [_post_preview(p) for p in posts]


@router.get("/dashboard", response_model=DashboardStatsResponse)
def dashboard_stats(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    org_id = user.organization_id
    start, end = _today_range_kst()

    new_today = (
        db.scalar(
            select(func.count())
            .select_from(Post)
            .where(
                Post.organization_id == org_id,
                Post.collected_at >= start,
                Post.collected_at < end,
                Post.status != PostStatus.deleted,
            )
        )
        or 0
    )

    trusted_count = (
        db.scalar(
            select(func.count())
            .select_from(Post)
            .where(
                Post.organization_id == org_id,
                Post.board_type == BoardType.trusted,
                Post.status != PostStatus.deleted,
            )
        )
        or 0
    )

    high_importance = (
        db.scalar(
            select(func.count())
            .select_from(Post)
            .where(
                Post.organization_id == org_id,
                Post.importance == Importance.high,
                Post.status.in_([PostStatus.published, PostStatus.pending]),
            )
        )
        or 0
    )

    pending_discovery = PostService(db).pending_count(org_id)

    active_sources = (
        db.scalar(
            select(func.count())
            .select_from(Source)
            .where(Source.organization_id == org_id, Source.is_active.is_(True))
        )
        or 0
    )
    total_sources = (
        db.scalar(select(func.count()).select_from(Source).where(Source.organization_id == org_id)) or 0
    )

    latest_report_row = db.scalar(
        select(DailyReport)
        .where(DailyReport.organization_id == org_id)
        .order_by(DailyReport.report_date.desc())
        .limit(1)
    )
    latest_report = (
        DashboardLatestReport(
            id=str(latest_report_row.id),
            title=latest_report_row.title,
            report_date=latest_report_row.report_date.isoformat(),
            slack_sent=latest_report_row.slack_sent,
        )
        if latest_report_row
        else None
    )

    return DashboardStatsResponse(
        new_today=new_today,
        trusted_count=trusted_count,
        pending_discovery=pending_discovery,
        high_importance=high_importance,
        active_sources=active_sources,
        total_sources=total_sources,
        latest_report=latest_report,
        trusted_preview=_recent_posts(db, org_id, board_type=BoardType.trusted, limit=5),
        discovery_preview=_recent_posts(
            db, org_id, board_type=BoardType.discovery, status=PostStatus.pending, limit=5
        ),
    )
