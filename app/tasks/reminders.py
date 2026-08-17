"""Celery task for scheduled reminder evaluation and notification dispatch."""

import logging
import asyncpg
from app.celery_app import celery_app
from app.config import get_settings
from app.services.reminder_service import ReminderService

logger = logging.getLogger("app.tasks.reminders")


async def _run_due_reminders_check() -> int:
    """Evaluates due reminders using a task-scoped connection pool."""
    settings = get_settings()
    pool = await asyncpg.create_pool(
        dsn=settings.postgres_dsn,
        min_size=1,
        max_size=2,
        command_timeout=settings.DB_POOL_TIMEOUT,
    )
    try:
        service = ReminderService(pool)
        return await service.process_due_reminders()
    finally:
        await pool.close()


@celery_app.task(name="app.tasks.reminders.check_due_reminders_task")
def check_due_reminders_task() -> int:
    """Periodic task executed by Celery Beat every 30s to check and fire due reminders."""
    logger.info("Executing scheduled check for due reminders...")
    try:
        from app.tasks.triage import run_coroutine_sync
        count = run_coroutine_sync(_run_due_reminders_check())
        logger.info("Reminder check completed. Evaluated %d reminders.", count)
        return count
    except Exception as exc:
        logger.error("Error executing due reminders check: %s", str(exc), exc_info=True)
        return 0
