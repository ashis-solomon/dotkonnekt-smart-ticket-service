"""Celery application and Celery Beat periodic task scheduling."""

from celery import Celery
from app.config import get_settings

settings = get_settings()

celery_app = Celery(
    "smart_ticket_service",
    broker=settings.celery_broker_dsn,
    backend=settings.celery_backend_dsn,
    include=[
        "app.tasks.triage",
        "app.tasks.reminders",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=30,  # 30 seconds hard limit
    beat_schedule={
        "check-due-reminders-every-30s": {
            "task": "app.tasks.reminders.check_due_reminders_task",
            "schedule": 30.0,
        },
    },
)
