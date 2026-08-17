"""Reminder service handling event scheduling, evaluation, and user notifications."""

import logging
from typing import Any, List, Optional, Tuple
from uuid import UUID
import asyncpg
from fastapi import HTTPException, status
import uuid6

from app.schemas.reminder import (
    NotificationResponse,
    ReminderCreate,
    ReminderResponse,
    ReminderStatus,
)
from app.schemas.ticket import TicketStatus
from app.schemas.user import UserResponse
from app.services.ticket_service import TicketService

logger = logging.getLogger("app.services.reminder")


class ReminderService:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def create_reminder(
        self, ticket_id: UUID, data: ReminderCreate, user: UserResponse
    ) -> ReminderResponse:
        """Schedules a new event reminder for a ticket."""
        ticket_service = TicketService(self.pool)
        # Enforce ticket ownership/role access
        await ticket_service.get_ticket(ticket_id, user)

        reminder_id = uuid6.uuid7()
        query = """
            INSERT INTO reminders (id, ticket_id, created_by_id, scheduled_for, message, status, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5, 'pending', NOW(), NOW())
            RETURNING id, ticket_id, created_by_id, scheduled_for, message, status, created_at, updated_at
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                query,
                reminder_id,
                ticket_id,
                user.id,
                data.scheduled_for,
                data.message,
            )

        return ReminderResponse(**dict(row))

    async def list_reminders(self, ticket_id: UUID, user: UserResponse) -> List[ReminderResponse]:
        """Lists all reminders attached to a ticket."""
        ticket_service = TicketService(self.pool)
        await ticket_service.get_ticket(ticket_id, user)

        query = """
            SELECT id, ticket_id, created_by_id, scheduled_for, message, status, created_at, updated_at
            FROM reminders
            WHERE ticket_id = $1
            ORDER BY scheduled_for ASC
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, ticket_id)

        return [ReminderResponse(**dict(r)) for r in rows]

    async def process_due_reminders(self) -> int:
        """
        Evaluates due reminders.
        If ticket is resolved/closed -> cancels reminder.
        If ticket is active -> fires reminder and inserts notification record.
        """
        query = """
            SELECT r.id, r.ticket_id, r.created_by_id, r.message,
                   t.status as ticket_status, t.assigned_to_id
            FROM reminders r
            JOIN tickets t ON r.ticket_id = t.id
            WHERE r.status = 'pending' AND r.scheduled_for <= NOW()
        """
        processed_count = 0
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query)

            for row in rows:
                reminder_id = row["id"]
                ticket_status = row["ticket_status"]

                if ticket_status in [TicketStatus.RESOLVED.value, TicketStatus.CLOSED.value]:
                    # Cancel reminder
                    await conn.execute(
                        "UPDATE reminders SET status = 'cancelled', updated_at = NOW() WHERE id = $1",
                        reminder_id,
                    )
                    logger.info("Cancelled due reminder %s because ticket %s is %s",
                                reminder_id, row["ticket_id"], ticket_status)
                else:
                    # Fire reminder
                    await conn.execute(
                        "UPDATE reminders SET status = 'fired', updated_at = NOW() WHERE id = $1",
                        reminder_id,
                    )
                    # Notify assignee, or reminder creator if unassigned
                    recipient_id = row["assigned_to_id"] or row["created_by_id"]
                    notif_id = uuid6.uuid7()
                    notif_msg = f"Reminder for ticket #{str(row['ticket_id'])[:8]}: {row['message']}"

                    await conn.execute(
                        """
                        INSERT INTO notifications (id, reminder_id, user_id, message, created_at)
                        VALUES ($1, $2, $3, $4, NOW())
                        """,
                        notif_id,
                        reminder_id,
                        recipient_id,
                        notif_msg,
                    )
                    logger.info("Fired reminder %s and logged notification for user %s", reminder_id, recipient_id)

                processed_count += 1

        return processed_count

    async def list_notifications(
        self,
        user: UserResponse,
        target_user_id: Optional[UUID] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[NotificationResponse], int]:
        """Lists paginated notifications: Admin views all (or filtered by user_id), Agent views only their own."""
        conditions = ["1=1"]
        params: List[Any] = []
        idx = 1

        if user.is_agent:
            # Agents can only view their own notifications
            conditions.append(f"user_id = ${idx}")
            params.append(user.id)
            idx += 1
        elif target_user_id:
            # Admins can optionally filter by a specific user ID
            conditions.append(f"user_id = ${idx}")
            params.append(target_user_id)
            idx += 1

        where_clause = " AND ".join(conditions)

        count_query = f"SELECT COUNT(*) FROM notifications WHERE {where_clause}"
        async with self.pool.acquire() as conn:
            total_records = await conn.fetchval(count_query, *params)

            offset = (page - 1) * page_size
            data_query = f"""
                SELECT id, reminder_id, user_id, message, created_at
                FROM notifications
                WHERE {where_clause}
                ORDER BY created_at DESC
                LIMIT ${idx} OFFSET ${idx+1}
            """
            rows = await conn.fetch(data_query, *params, page_size, offset)

        notifications = [NotificationResponse(**dict(r)) for r in rows]
        return notifications, total_records or 0
