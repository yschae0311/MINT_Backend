"""Celery queue names — interactive jobs must not wait behind ES index backlog."""

INTERACTIVE_QUEUE = "interactive"
BACKGROUND_QUEUE = "background"
DEFAULT_QUEUE = "celery"

INTERACTIVE_TASKS = (
    "app.workers.tasks.crawl_all_discovery_job_task",
    "app.workers.tasks.crawl_source_job_task",
    "app.workers.tasks.generate_report_job_task",
    "app.workers.tasks.classify_posts_job_task",
    "app.workers.tasks.generate_personal_reports_job_task",
)

BACKGROUND_TASKS = (
    "app.workers.tasks.process_search_index_queue_task",
)
