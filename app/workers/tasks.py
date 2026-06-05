from datetime import date, timedelta
from uuid import UUID

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.organization import Organization
from app.models.post import Post
from app.services.ai_service import AIService
from app.services.crawler_service import CrawlerService
from app.services.report_service import ReportService
from app.services.slack_service import SlackService
from app.workers.celery_app import celery_app


@celery_app.task(name="app.workers.tasks.crawl_all_sources_task")
def crawl_all_sources_task():
    db = SessionLocal()
    try:
        for org in db.scalars(select(Organization)).all():
            CrawlerService(db).crawl_all_active(org.id)
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
            CrawlerService(db).crawl_all_active_to_discovery(org.id, trusted_only=trusted_only)
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
        target = date.fromisoformat(report_date) if report_date else date.today() - timedelta(days=1)
        for org in db.scalars(select(Organization)).all():
            try:
                ReportService(db).generate(org.id, target, prefer_yesterday=True)
            except Exception:
                continue
            recent = db.scalars(select(Post).where(Post.organization_id == org.id).limit(20)).all()
            for post in recent:
                if not post.ai_outputs:
                    try:
                        AIService(db).summarize_post(post.id, org.id)
                    except Exception:
                        pass
    finally:
        db.close()


@celery_app.task(name="app.workers.tasks.send_daily_report_to_slack_task")
def send_daily_report_to_slack_task():
    from app.models.daily_report import DailyReport

    db = SessionLocal()
    try:
        target = date.today() - timedelta(days=1)
        reports = db.scalars(select(DailyReport).where(DailyReport.report_date == target)).all()
        for report in reports:
            try:
                SlackService(db).send_report(report.id, report.organization_id)
            except Exception:
                pass
    finally:
        db.close()
