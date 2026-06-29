from celery import Celery
from celery.schedules import crontab

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery("mint", broker=settings.redis_url, backend=settings.redis_url)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Seoul",
    enable_utc=True,
)
celery_app.autodiscover_tasks(["app.workers"])

celery_app.conf.beat_schedule = {
    "purge-stale-discovery-posts": {
        "task": "app.workers.tasks.purge_stale_discovery_posts_task",
        "schedule": crontab(hour=5, minute=30),
    },
    "daily-discovery-pipeline": {
        "task": "app.workers.tasks.discovery_pipeline_task",
        "schedule": crontab(hour=6, minute=0),
    },
    "crawl-all-sources": {
        "task": "app.workers.tasks.crawl_all_sources_task",
        "schedule": crontab(hour=6, minute=0),
    },
    "community-discovery-pipeline": {
        "task": "app.workers.tasks.community_discovery_pipeline_task",
        "schedule": crontab(hour=6, minute=30),
    },
    "generate-daily-report": {
        "task": "app.workers.tasks.generate_daily_report_task",
        "schedule": crontab(hour=8, minute=0),
    },
    "generate-personal-reports": {
        "task": "app.workers.tasks.generate_personal_reports_task",
        "schedule": crontab(hour=8, minute=10),
    },
    "send-daily-report-slack": {
        "task": "app.workers.tasks.send_daily_report_to_slack_task",
        "schedule": crontab(hour=8, minute=30),
    },
    "process-search-index-queue": {
        "task": "app.workers.tasks.process_search_index_queue_task",
        "schedule": 60.0,
    },
}
