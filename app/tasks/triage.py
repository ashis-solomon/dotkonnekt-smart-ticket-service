"""Celery task for non-blocking LLM ticket triage with exponential backoff retries and fallback."""

import asyncio
import concurrent.futures
from contextlib import asynccontextmanager
import logging
from typing import AsyncIterator, Optional
from uuid import UUID
import asyncpg
from celery.exceptions import MaxRetriesExceededError
from app.celery_app import celery_app
from app.config import get_settings
from app.services.llm_service import get_llm_adapter

logger = logging.getLogger("app.tasks.triage")


def run_coroutine_sync(coro):
    """Safely executes an async coroutine from synchronous Celery worker or running event loop."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(asyncio.run, coro).result()


@asynccontextmanager
async def _get_conn(pool: Optional[asyncpg.Pool] = None) -> AsyncIterator[asyncpg.Connection]:
    """Helper providing an asyncpg connection scoped to the active event loop."""
    if pool:
        async with pool.acquire() as conn:
            yield conn
    else:
        conn = await asyncpg.connect(dsn=get_settings().postgres_dsn)
        try:
            yield conn
        finally:
            await conn.close()


async def _execute_triage(ticket_id: str, pool: Optional[asyncpg.Pool] = None) -> None:
    """Async database operations for LLM ticket triage."""
    uuid_obj = UUID(ticket_id)
    adapter = get_llm_adapter()

    async with _get_conn(pool) as conn:
        row = await conn.fetchrow(
            "SELECT id, title, description, priority, category FROM tickets WHERE id = $1",
            uuid_obj,
        )
        if not row:
            logger.warning("Ticket %s not found for triage.", ticket_id)
            return

        triage_result = await adapter.triage(title=row["title"], description=row["description"])

        new_priority = triage_result.priority if not row["priority"] or row["priority"] == "medium" else row["priority"]
        new_category = triage_result.category if not row["category"] else row["category"]

        update_query = """
            UPDATE tickets
            SET category = $1,
                priority = $2,
                ai_summary = $3,
                manual_triage_required = FALSE,
                updated_at = NOW()
            WHERE id = $4
        """
        await conn.execute(update_query, new_category, new_priority, triage_result.summary, uuid_obj)
        logger.info("Triaged ticket %s: category=%s, priority=%s", ticket_id, new_category, new_priority)


async def _mark_manual_triage_required(ticket_id: str, pool: Optional[asyncpg.Pool] = None) -> None:
    """Flags ticket as requiring manual triage after all retries fail."""
    uuid_obj = UUID(ticket_id)
    async with _get_conn(pool) as conn:
        await conn.execute(
            "UPDATE tickets SET manual_triage_required = TRUE, updated_at = NOW() WHERE id = $1",
            uuid_obj,
        )
        logger.warning("Ticket %s flagged for manual triage.", ticket_id)


@celery_app.task(
    bind=True,
    name="app.tasks.triage.triage_ticket_task",
    max_retries=3,
    default_retry_delay=2,
)
def triage_ticket_task(self, ticket_id: str) -> None:
    """Celery task executing LLM auto-triage with exponential backoff and graceful manual fallback."""
    attempt = getattr(self.request, "retries", 0) + 1
    logger.info("Starting auto-triage for ticket ID: %s (attempt %d/4)", ticket_id, attempt)
    try:
        run_coroutine_sync(_execute_triage(ticket_id))
    except Exception as exc:
        logger.error("Error during triage for ticket %s: %s", ticket_id, str(exc))
        if self.request.retries >= self.max_retries:
            logger.warning("Max retries (%d) reached for ticket %s. Setting manual_triage_required = TRUE.", self.max_retries, ticket_id)
            run_coroutine_sync(_mark_manual_triage_required(ticket_id))
        else:
            countdown = 2 ** (self.request.retries + 1)
            logger.info("Scheduling retry for ticket %s in %ds...", ticket_id, countdown)
            raise self.retry(exc=exc, countdown=countdown)
