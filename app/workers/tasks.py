import logging
from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.core.database import SessionLocal
from app.models.enums import AccountApprovalStatus, JobType, PostStatus, SourceType, TrustLevel
from app.models.organization import Organization
from app.models.post import Post
from app.models.source import Source
from app.models.user import User
from app.services.ai_service import AIService
from app.services.community_sources import COMMUNITY_SOURCE_TYPES
from app.services.crawl_progress import CrawlProgressTracker, estimate_discovery_candidates_per_source
from app.services.crawl_skip_stats import CrawlSkipStats
from app.services.crawler_service import CrawlerService
from app.services.job_service import JobService
from app.services.post_service import PostService
from app.services.personalization_service import ClassificationService, PersonalReportService
from app.services.report_service import ReportService
from app.services.slack_service import SlackService
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")


def _kst_today() -> date:
    return datetime.now(KST).date()


def _discovery_sources_query(
    organization_id: UUID,
    *,
    to_discovery: bool,
    trusted_only: bool,
    community_only: bool = False,
    exclude_community: bool = False,
):
    q = select(Source).where(Source.organization_id == organization_id, Source.is_active.is_(True))
    if community_only:
        q = q.where(Source.source_type.in_(tuple(COMMUNITY_SOURCE_TYPES)))
    elif to_discovery and trusted_only:
        q = q.where(Source.trust_level == TrustLevel.high)
    if exclude_community and not community_only:
        q = q.where(Source.source_type.not_in(tuple(COMMUNITY_SOURCE_TYPES)))
        q = q.where(Source.trust_level != TrustLevel.low)
    return q


def _run_crawl_all(
    db,
    jobs: JobService,
    job_id: UUID,
    organization_id: UUID,
    *,
    to_discovery: bool,
    trusted_only: bool,
    community_only: bool = False,
    exclude_community: bool = False,
) -> str:
    crawler = CrawlerService(db)
    q = _discovery_sources_query(
        organization_id,
        to_discovery=to_discovery,
        trusted_only=trusted_only,
        community_only=community_only,
        exclude_community=exclude_community,
    )
    sources = list(db.scalars(q).all())
    created_sum = 0
    stats = CrawlSkipStats()
    failed = 0

    if to_discovery:
        estimated_total = sum(estimate_discovery_candidates_per_source(s) for s in sources) or 1
        progress = CrawlProgressTracker(
            jobs,
            job_id,
            source_total=len(sources),
            estimated_candidate_total=estimated_total,
        )
        progress.begin()
    else:
        progress = None
        total = len(sources)
        jobs.update_progress(job_id, 0, total, f"0/{total} 소스 준비")

    for i, source in enumerate(sources, start=1):
        if jobs.is_cancelled(job_id):
            return "사용자에 의해 취소됨"
        if to_discovery:
            progress.on_source_start(i, source.name)
        else:
            jobs.update_progress(job_id, i - 1, total, f"{i}/{total} · {source.name}")
        result = crawler._crawl_source_safe(
            source,
            organization_id,
            to_discovery=to_discovery,
            progress=progress,
        )
        created_sum += result.created
        for reason, count in (result.skip_reasons or {}).items():
            stats.add(reason, count)
        if result.error_sample and not stats.error_sample:
            stats.error_sample = result.error_sample
        if result.error:
            failed += 1
            stats.add("source_error")

    if to_discovery and progress is not None:
        progress.finish()
    else:
        jobs.update_progress(job_id, total, total, "완료")
    return stats.format_summary(created_sum, failed_sources=failed)


@celery_app.task(name="app.workers.tasks.process_search_index_queue_task")
def process_search_index_queue_task(batch_size: int = 50):
    from app.search.index_outbox import process_search_index_queue

    db = SessionLocal()
    try:
        ok, failed = process_search_index_queue(db, batch_size=batch_size)
        if ok or failed:
            logger.info("search index queue processed ok=%s failed=%s", ok, failed)
    except Exception as exc:
        logger.exception("process_search_index_queue_task failed: %s", exc)
        db.rollback()
    finally:
        db.close()


@celery_app.task(name="app.workers.tasks.crawl_source_job_task")
def crawl_source_job_task(job_id: str, source_id: str, organization_id: str, to_discovery: bool = False):
    db = SessionLocal()
    try:
        jobs = JobService(db)
        jobs.start_job(UUID(job_id))
        if jobs.is_cancelled(UUID(job_id)):
            return
        crawler = CrawlerService(db)
        progress = None
        if to_discovery:
            source = db.get(Source, UUID(source_id))
            estimated = estimate_discovery_candidates_per_source(source) if source else 1
            progress = CrawlProgressTracker(
                jobs,
                UUID(job_id),
                source_total=1,
                estimated_candidate_total=estimated,
            )
            progress.begin(source_name=source.name if source else None)
        else:
            jobs.update_progress(UUID(job_id), 0, 1, "크롤링 중…")
        if to_discovery:
            result = crawler.crawl_source_to_discovery(
                UUID(source_id), UUID(organization_id), progress=progress
            )
            if progress is not None:
                progress.finish()
        else:
            result = crawler.crawl_source(UUID(source_id), UUID(organization_id))
        msg = result.message or f"created {result.created}, skipped {result.skipped}"
        if result.error:
            jobs.fail_job(UUID(job_id), result.error)
        elif jobs.is_cancelled(UUID(job_id)):
            return
        else:
            jobs.complete_job(UUID(job_id), msg)
    except Exception as exc:
        logger.exception("crawl_source_job_task failed job=%s", job_id)
        JobService(db).fail_job(UUID(job_id), str(exc))
    finally:
        db.close()


@celery_app.task(name="app.workers.tasks.crawl_all_discovery_job_task")
def crawl_all_discovery_job_task(
    job_id: str,
    organization_id: str,
    trusted_only: bool = True,
    community_only: bool = False,
):
    db = SessionLocal()
    try:
        jobs = JobService(db)
        jobs.start_job(UUID(job_id))
        if jobs.is_cancelled(UUID(job_id)):
            return
        q = _discovery_sources_query(
            UUID(organization_id),
            to_discovery=True,
            trusted_only=trusted_only,
            community_only=community_only,
        )
        sources = list(db.scalars(q).all())
        estimated = sum(estimate_discovery_candidates_per_source(s) for s in sources) or 1
        jobs.update_progress(
            UUID(job_id),
            0,
            estimated,
            f"0 / {estimated}건 · 준비",
        )
        msg = _run_crawl_all(
            db,
            jobs,
            UUID(job_id),
            UUID(organization_id),
            to_discovery=True,
            trusted_only=trusted_only,
            community_only=community_only,
        )
        if jobs.is_cancelled(UUID(job_id)):
            return
        jobs.complete_job(UUID(job_id), msg)
    except Exception as exc:
        logger.exception("crawl_all_discovery_job_task failed job=%s", job_id)
        JobService(db).fail_job(UUID(job_id), str(exc))
    finally:
        db.close()


@celery_app.task(name="app.workers.tasks.generate_report_job_task")
def generate_report_job_task(job_id: str, organization_id: str, report_date: str | None = None):
    db = SessionLocal()
    try:
        jobs = JobService(db)
        jobs.start_job(UUID(job_id))
        if jobs.is_cancelled(UUID(job_id)):
            return
        jobs.update_progress(UUID(job_id), 0, 1, "리포트 생성 중…")
        target = date.fromisoformat(report_date) if report_date else _kst_today()
        report = ReportService(db).generate(
            UUID(organization_id),
            target,
            prefer_yesterday=False,
            allow_empty=True,
        )
        if jobs.is_cancelled(UUID(job_id)):
            return
        jobs.complete_job(UUID(job_id), f"리포트 생성 완료 ({report.report_date})")
    except Exception as exc:
        logger.exception("generate_report_job_task failed job=%s", job_id)
        JobService(db).fail_job(UUID(job_id), str(exc))
    finally:
        db.close()


@celery_app.task(name="app.workers.tasks.crawl_all_sources_task")
def crawl_all_sources_task():
    db = SessionLocal()
    try:
        for org in db.scalars(select(Organization)).all():
            jobs = JobService(db)
            job = jobs.create_job(
                org.id,
                JobType.crawl_all,
                "전체 소스 크롤링 (스케줄)",
                progress_total=1,
            )
            db.commit()
            try:
                jobs.start_job(job.id)
                msg = _run_crawl_all(
                    db,
                    jobs,
                    job.id,
                    org.id,
                    to_discovery=False,
                    trusted_only=False,
                    exclude_community=True,
                )
                jobs.complete_job(job.id, msg)
            except Exception as exc:
                logger.exception("crawl_all_sources org=%s failed", org.id)
                jobs.fail_job(job.id, str(exc))
    finally:
        db.close()


@celery_app.task(name="app.workers.tasks.purge_stale_discovery_posts_task")
def purge_stale_discovery_posts_task(retention_days: int | None = None):
    from app.services.org_settings_service import OrgSettingsService

    db = SessionLocal()
    try:
        for org in db.scalars(select(Organization)).all():
            days = (
                retention_days
                if retention_days is not None
                else OrgSettingsService(db).discovery_pending_retention_days(org.id)
            )
            if days <= 0:
                logger.info("purge_stale_discovery skipped org=%s (retention_days=%s)", org.id, days)
                continue

            jobs = JobService(db)
            job = jobs.create_job(
                org.id,
                JobType.purge_stale_discovery,
                f"미승인 AI 발견 정리 ({days}일 초과)",
                progress_total=1,
            )
            db.commit()
            try:
                jobs.start_job(job.id)
                jobs.update_progress(job.id, 0, 1, "만료 후보 검색 중…")
                count = PostService(db).purge_stale_pending_discovery(org.id, retention_days=days)
                jobs.complete_job(job.id, f"삭제 {count}건")
                if count:
                    logger.info("purge_stale_discovery org=%s deleted=%s", org.id, count)
            except Exception as exc:
                logger.exception("purge_stale_discovery org=%s failed", org.id)
                jobs.fail_job(job.id, str(exc))
    finally:
        db.close()


@celery_app.task(name="app.workers.tasks.discovery_pipeline_task")
def discovery_pipeline_task(trusted_only: bool = True):
    db = SessionLocal()
    try:
        for org in db.scalars(select(Organization)).all():
            jobs = JobService(db)
            job = jobs.create_job(
                org.id,
                JobType.discovery_pipeline,
                "AI 발견 파이프라인 (스케줄)",
                progress_total=1,
            )
            db.commit()
            try:
                jobs.start_job(job.id)
                msg = _run_crawl_all(
                    db,
                    jobs,
                    job.id,
                    org.id,
                    to_discovery=True,
                    trusted_only=trusted_only,
                )
                jobs.complete_job(job.id, msg)
            except Exception as exc:
                logger.exception("discovery_pipeline org=%s failed", org.id)
                jobs.fail_job(job.id, str(exc))
    finally:
        db.close()


@celery_app.task(name="app.workers.tasks.community_discovery_pipeline_task")
def community_discovery_pipeline_task():
    db = SessionLocal()
    try:
        for org in db.scalars(select(Organization)).all():
            jobs = JobService(db)
            job = jobs.create_job(
                org.id,
                JobType.community_discovery_pipeline,
                "커뮤니티 탐문 파이프라인 (스케줄)",
                progress_total=1,
            )
            db.commit()
            try:
                jobs.start_job(job.id)
                msg = _run_crawl_all(
                    db,
                    jobs,
                    job.id,
                    org.id,
                    to_discovery=True,
                    trusted_only=False,
                    community_only=True,
                )
                jobs.complete_job(job.id, msg)
            except Exception as exc:
                logger.exception("community_discovery_pipeline org=%s failed", org.id)
                jobs.fail_job(job.id, str(exc))
    finally:
        db.close()


@celery_app.task(name="app.workers.tasks.crawl_source_task")
def crawl_source_task(source_id: str, organization_id: str):
    db = SessionLocal()
    try:
        CrawlerService(db).crawl_source(UUID(source_id), UUID(organization_id))
    finally:
        db.close()


@celery_app.task(name="app.workers.tasks.summarize_post_task")
def summarize_post_task(post_id: str, organization_id: str):
    db = SessionLocal()
    try:
        AIService(db).summarize_post(UUID(post_id), UUID(organization_id))
    finally:
        db.close()


@celery_app.task(name="app.workers.tasks.classify_posts_job_task")
def classify_posts_job_task(job_id: str, organization_id: str, limit: int = 500):
    db = SessionLocal()
    try:
        jobs = JobService(db)
        jobs.start_job(UUID(job_id))
        if jobs.is_cancelled(UUID(job_id)):
            return
        q = (
            select(Post)
            .options(joinedload(Post.ai_outputs))
            .where(
                Post.organization_id == UUID(organization_id),
                Post.status.not_in([PostStatus.deleted, PostStatus.hidden]),
            )
        )
        posts = list(db.scalars(q.order_by(Post.collected_at.desc()).limit(limit)).unique().all())
        total = len(posts)
        jobs.update_progress(UUID(job_id), 0, max(total, 1), f"0 / {total} 분류 중…")
        ok = 0
        failed = 0
        for index, post in enumerate(posts, start=1):
            if jobs.is_cancelled(UUID(job_id)):
                return
            try:
                ClassificationService(db).classify_post(post)
                db.commit()
                ok += 1
            except Exception as exc:
                logger.warning("classify post=%s failed: %s", post.id, exc)
                db.rollback()
                failed += 1
            jobs.update_progress(UUID(job_id), index, total, f"{index} / {total} 분류 중…")
        suffix = f", 실패 {failed}건" if failed else ""
        jobs.complete_job(UUID(job_id), f"재분류 완료 (성공 {ok}건{suffix})")
    except Exception as exc:
        logger.exception("classify_posts_job_task failed job=%s", job_id)
        JobService(db).fail_job(UUID(job_id), str(exc))
    finally:
        db.close()


@celery_app.task(name="app.workers.tasks.classify_existing_posts_task")
def classify_existing_posts_task(organization_id: str | None = None, limit: int = 500):
    db = SessionLocal()
    try:
        q = (
            select(Post)
            .options(joinedload(Post.ai_outputs))
            .where(Post.status.not_in([PostStatus.deleted, PostStatus.hidden]))
        )
        if organization_id:
            q = q.where(Post.organization_id == UUID(organization_id))
        posts = list(db.scalars(q.order_by(Post.collected_at.desc()).limit(limit)).unique().all())
        for post in posts:
            try:
                ClassificationService(db).classify_post(post)
                db.commit()
            except Exception as exc:
                logger.warning("classify post=%s failed: %s", post.id, exc)
                db.rollback()
    finally:
        db.close()


@celery_app.task(name="app.workers.tasks.generate_personal_report_job_task")
def generate_personal_report_job_task(job_id: str, user_id: str, report_date: str | None = None):
    db = SessionLocal()
    try:
        jobs = JobService(db)
        jobs.start_job(UUID(job_id))
        if jobs.is_cancelled(UUID(job_id)):
            return
        jobs.update_progress(UUID(job_id), 0, 1, "개인 리포트 생성 중…")
        user = db.get(User, UUID(user_id))
        if not user:
            jobs.fail_job(UUID(job_id), "사용자를 찾을 수 없습니다.")
            return
        target = date.fromisoformat(report_date) if report_date else _kst_today()
        report = PersonalReportService(db).generate_for_user(user, target)
        if jobs.is_cancelled(UUID(job_id)):
            return
        if report:
            jobs.complete_job(UUID(job_id), f"개인 리포트 생성 완료 ({report.report_date})")
        else:
            jobs.fail_job(
                UUID(job_id),
                "오늘 매칭 기사가 없거나 관심 키워드가 부족합니다.",
            )
    except Exception as exc:
        logger.exception("generate_personal_report_job_task failed job=%s", job_id)
        JobService(db).fail_job(UUID(job_id), str(exc))
    finally:
        db.close()


@celery_app.task(name="app.workers.tasks.generate_personal_reports_task")
def generate_personal_reports_task(report_date: str | None = None):
    db = SessionLocal()
    try:
        target = date.fromisoformat(report_date) if report_date else _kst_today()
        users = db.scalars(
            select(User).where(
                User.is_active.is_(True),
                User.approval_status == AccountApprovalStatus.approved,
            )
        ).all()
        for user in users:
            try:
                PersonalReportService(db).generate_for_user(user, target)
            except Exception as exc:
                logger.warning(
                    "personal report user=%s date=%s failed: %s",
                    user.id,
                    target,
                    exc,
                )
                db.rollback()
    finally:
        db.close()


@celery_app.task(name="app.workers.tasks.generate_daily_report_task")
def generate_daily_report_task(report_date: str | None = None):
    db = SessionLocal()
    try:
        target = date.fromisoformat(report_date) if report_date else _kst_today()
        logger.info("generate_daily_report target=%s", target)
        for org in db.scalars(select(Organization)).all():
            jobs = JobService(db)
            job = jobs.create_job(
                org.id,
                JobType.generate_report,
                f"데일리 리포트 생성 (스케줄 · {target})",
                progress_total=1,
            )
            db.commit()
            try:
                jobs.start_job(job.id)
                jobs.update_progress(job.id, 0, 1, "리포트 생성 중…")
                report = ReportService(db).generate(org.id, target, allow_empty=True)
                jobs.complete_job(job.id, f"리포트 생성 완료 ({report.report_date})")
            except Exception as exc:
                logger.warning("generate_daily_report org=%s date=%s failed: %s", org.id, target, exc)
                jobs.fail_job(job.id, str(exc))
                continue
            recent = db.scalars(select(Post).where(Post.organization_id == org.id).limit(20)).all()
            for post in recent:
                if not post.ai_outputs:
                    try:
                        AIService(db).summarize_post(post.id, org.id)
                    except Exception as exc:
                        logger.warning("summarize_post post=%s failed: %s", post.id, exc)
    finally:
        db.close()


@celery_app.task(name="app.workers.tasks.send_daily_report_to_slack_task")
def send_daily_report_to_slack_task(report_date: str | None = None):
    from app.models.daily_report import DailyReport

    db = SessionLocal()
    try:
        target = date.fromisoformat(report_date) if report_date else _kst_today()
        reports = db.scalars(select(DailyReport).where(DailyReport.report_date == target)).all()
        reports_by_org = {r.organization_id: r for r in reports}

        for org in db.scalars(select(Organization)).all():
            jobs = JobService(db)
            job = jobs.create_job(
                org.id,
                JobType.send_slack_report,
                f"Slack 리포트 전송 (스케줄 · {target})",
                progress_total=1,
            )
            db.commit()
            try:
                jobs.start_job(job.id)
                jobs.update_progress(job.id, 0, 1, "Slack 전송 중…")
                report = reports_by_org.get(org.id)
                if report:
                    result = SlackService(db).send_report(report.id, org.id)
                else:
                    result = SlackService(db).send_no_changes(org.id, target)
                if result.success:
                    jobs.complete_job(
                        job.id,
                        "Slack 전송 완료" if report else "변경 없음 알림 전송 완료",
                    )
                    logger.info(
                        "send_daily_report_to_slack org=%s sent (%s)",
                        org.id,
                        "report" if report else "no-changes",
                    )
                else:
                    jobs.fail_job(job.id, result.message)
                    logger.warning(
                        "send_daily_report_to_slack org=%s failed: %s",
                        org.id,
                        result.message,
                    )
            except Exception as exc:
                logger.warning("send_daily_report_to_slack org=%s failed: %s", org.id, exc)
                jobs.fail_job(job.id, str(exc))
    finally:
        db.close()


@celery_app.task(name="app.workers.tasks.daily_pipeline_task")
def daily_pipeline_task():
    """Run full daily pipeline in order: crawl -> report -> slack."""
    logger.info("daily_pipeline started")
    crawl_all_sources_task()
    discovery_pipeline_task()
    generate_daily_report_task()
    generate_personal_reports_task()
    send_daily_report_to_slack_task()
    logger.info("daily_pipeline finished")
