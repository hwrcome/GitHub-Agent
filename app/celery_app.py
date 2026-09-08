from celery import Celery

from app.config import get_settings


settings = get_settings()
celery_app = Celery("github_agent", broker=settings.broker_url, backend=settings.broker_url)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    enable_utc=True,
    timezone="UTC",
    task_time_limit=900,
    task_soft_time_limit=840,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
)
