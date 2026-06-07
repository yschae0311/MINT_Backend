import logging
from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.organization import Organization
from app.models.post import Post
from app.services.ai_service import AIService
from app.services.crawler_service import CrawlerService
from app.services.report_service import ReportService
from app.services.slack_service import SlackService
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")


def _kst_yesterday() -> date:
    return datetime.now(KST).date() - timedelta(days=1)


@celery_app.task(name="app.workers.tasks.crawl_all_sources_task")
def crawl_all_sources_task():
    db = SessionLocal()
    try:
        for org in db.scalars(select(Organization)).all():
            results = CrawlerService(db).crawl_all_active(org.id)
            created = sum(r.created for r in results)
            errors = [r for r in results if r.error]
            logger.info(
                "crawl_all_sources org=%s sources=%d created=%d errors=%d",
                org.id,
                len(results),
                created,
                len(errors),
            )
            for r in errors:
                logger.warning("crawl_all_sources failed source=%s: %s", r.source_id, r.error)
    finally:
        db.close()


@celery_app.task(name="app.workers.tasks.discovery_pipeline_task")
def discovery_pipeline_task(trusted_only: bool = True):
    """
    Daily pipeline:
    trusted (important) sources crawl -> AI summary -> register into discovery board (pending).
    """
    db = SessionLocal()
    try:
        for org in db.scalars(select(Organization)).all():
            results = CrawlerService(db).crawl_all_active_to_discovery(org.id, trusted_only=trusted_only)
            created = sum(r.created for r in results)
            errors = [r for r in results if r.error]
            logger.info(
                "discovery_pipeline org=%s sources=%d created=%d errors=%d",
                org.id,
                len(results),
                created,
                len(errors),
            )
            for r in errors:
                logger.warning("discovery_pipeline failed source=%s: %s", r.source_id, r.error)
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


@celery_app.task(name="app.workers.tasks.generate_daily_report_task")
def generate_daily_report_task(report_date: str | None = None):
    db = SessionLocal()
    try:
        target = date.fromisoformat(report_date) if report_date else _kst_yesterday()
        logger.info("generate_daily_report target=%s", target)
        for org in db.scalars(select(Organization)).all():
            try:
                report = ReportService(db).generate(org.id, target)
                logger.info(
                    "generate_daily_report org=%s report_id=%s date=%s",
                    org.id,
                    report.id,
                    report.report_date,
                )
            except Exception as exc:
                logger.warning("generate_daily_report org=%s date=%s failed: %s", org.id, target, exc)
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
        target = date.fromisoformat(report_date) if report_date else _kst_yesterday()
        reports = db.scalars(select(DailyReport).where(DailyReport.report_date == target)).all()
        if not reports:
            logger.warning("send_daily_report_to_slack no reports for date=%s", target)
            return
        for report in reports:
            try:
                result = SlackService(db).send_report(report.id, report.organization_id)
                if result.success:
                    logger.info("send_daily_report_to_slack report=%s sent", report.id)
                else:
                    logger.warning(
                        "send_daily_report_to_slack report=%s failed: %s",
                        report.id,
                        result.message,
                    )
            except Exception as exc:
                logger.warning("send_daily_report_to_slack report=%s failed: %s", report.id, exc)
    finally:
        db.close()


@celery_app.task(name="app.workers.tasks.daily_pipeline_task")
def daily_pipeline_task():
    """Run full daily pipeline in order: crawl -> report -> slack."""
    logger.info("daily_pipeline started")
    crawl_all_sources_task()
    discovery_pipeline_task()
    generate_daily_report_task()
    send_daily_report_to_slack_task()
    logger.info("daily_pipeline finished")
