"""Ticket service handling CRUD, trigram fuzzy search, notes, and role-based access control."""

import logging
import math
from typing import Any, Dict, List, Optional, Tuple
from uuid import UUID
import asyncpg
from fastapi import HTTPException, status
import uuid6

from app.schemas.note import NoteCreate, NoteResponse
from app.schemas.ticket import (
    TicketCategory,
    TicketCreate,
    TicketPriority,
    TicketResponse,
    TicketStatus,
    TicketUpdate,
)
from app.schemas.user import UserResponse, UserRole

logger = logging.getLogger("app.services.ticket")


class TicketService:
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool

    async def create_ticket(self, data: TicketCreate, user: UserResponse) -> TicketResponse:
        """Creates a new ticket and asynchronously dispatches Celery LLM triage."""
        ticket_id = uuid6.uuid7()
        priority = data.priority.value if data.priority else TicketPriority.MEDIUM.value
        assigned_to_id = data.assigned_to_id or (user.id if user.is_agent else None)

        query = """
            INSERT INTO tickets (
                id, title, description, customer_email, status, priority,
                category, ai_summary, manual_triage_required, created_by_id,
                assigned_to_id, created_at, updated_at
            )
            VALUES ($1, $2, $3, $4, 'open', $5, NULL, NULL, FALSE, $6, $7, NOW(), NOW())
            RETURNING id, title, description, customer_email, status, priority,
                      category, ai_summary, manual_triage_required, created_by_id,
                      assigned_to_id, created_at, updated_at
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                query,
                ticket_id,
                data.title,
                data.description,
                data.customer_email,
                priority,
                user.id,
                assigned_to_id,
            )

        # Dispatch Celery background task for LLM triage non-blockingly
        try:
            from app.tasks.triage import triage_ticket_task
            triage_ticket_task.delay(str(ticket_id))
            logger.info("Dispatched LLM triage task for ticket %s", ticket_id)
        except Exception as e:
            logger.warning("Could not enqueue Celery triage task immediately: %s", str(e))

        return TicketResponse(**dict(row))

    async def list_tickets(
        self,
        user: UserResponse,
        q: Optional[str] = None,
        status_filter: Optional[TicketStatus] = None,
        priority_filter: Optional[TicketPriority] = None,
        category_filter: Optional[TicketCategory] = None,
        assigned_to_id: Optional[UUID] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[TicketResponse], int]:
        """Lists and searches tickets using pg_trgm fuzzy search and multi-filtering."""
        conditions = ["1=1"]
        params: List[Any] = []
        idx = 1

        # RBAC: Agents can only view tickets assigned to them
        if user.is_agent:
            conditions.append(f"assigned_to_id = ${idx}")
            params.append(user.id)
            idx += 1
        elif assigned_to_id:
            conditions.append(f"assigned_to_id = ${idx}")
            params.append(assigned_to_id)
            idx += 1

        if status_filter:
            conditions.append(f"status = ${idx}")
            params.append(status_filter.value)
            idx += 1

        if priority_filter:
            conditions.append(f"priority = ${idx}")
            params.append(priority_filter.value)
            idx += 1

        if category_filter:
            conditions.append(f"category = ${idx}")
            params.append(category_filter.value)
            idx += 1

        order_by = "created_at DESC"
        if q and q.strip():
            search_term = q.strip()
            # Trigram fuzzy match and keyword containment across title and description
            conditions.append(
                f"(title % ${idx} OR description % ${idx} OR title ILIKE ${idx+1} OR description ILIKE ${idx+1})"
            )
            params.append(search_term)
            params.append(f"%{search_term}%")
            order_by = f"similarity(title, ${idx}) + similarity(description, ${idx}) DESC, created_at DESC"
            idx += 2

        where_clause = " AND ".join(conditions)

        # Count total records matching filters
        count_query = f"SELECT COUNT(*) FROM tickets WHERE {where_clause}"
        async with self.pool.acquire() as conn:
            total_records = await conn.fetchval(count_query, *params)

            offset = (page - 1) * page_size
            data_query = f"""
                SELECT id, title, description, customer_email, status, priority,
                       category, ai_summary, manual_triage_required, created_by_id,
                       assigned_to_id, created_at, updated_at
                FROM tickets
                WHERE {where_clause}
                ORDER BY {order_by}
                LIMIT ${idx} OFFSET ${idx+1}
            """
            rows = await conn.fetch(data_query, *params, page_size, offset)

        tickets = [TicketResponse(**dict(r)) for r in rows]
        return tickets, total_records or 0

    async def get_ticket(self, ticket_id: UUID, user: UserResponse) -> TicketResponse:
        """Retrieves ticket by ID while enforcing agent ownership boundaries."""
        query = """
            SELECT id, title, description, customer_email, status, priority,
                   category, ai_summary, manual_triage_required, created_by_id,
                   assigned_to_id, created_at, updated_at
            FROM tickets
            WHERE id = $1
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, ticket_id)

        if not row:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Ticket not found.",
            )

        ticket = TicketResponse(**dict(row))

        # RBAC Check: Agents can only view tickets assigned to them
        if user.is_agent and ticket.assigned_to_id != user.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Agents can only view tickets assigned to them.",
            )

        return ticket

    async def update_ticket(self, ticket_id: UUID, data: TicketUpdate, user: UserResponse) -> TicketResponse:
        """Updates ticket fields and auto-cancels reminders if resolved or closed."""
        # Ensure user has access to this ticket
        current_ticket = await self.get_ticket(ticket_id, user)

        updates: List[str] = []
        params: List[Any] = []
        idx = 1

        if data.title is not None:
            updates.append(f"title = ${idx}")
            params.append(data.title)
            idx += 1

        if data.description is not None:
            updates.append(f"description = ${idx}")
            params.append(data.description)
            idx += 1

        if data.status is not None:
            updates.append(f"status = ${idx}")
            params.append(data.status.value)
            idx += 1

        if data.priority is not None:
            updates.append(f"priority = ${idx}")
            params.append(data.priority.value)
            idx += 1

        if data.category is not None:
            updates.append(f"category = ${idx}")
            params.append(data.category.value)
            idx += 1

        if data.assigned_to_id is not None:
            # Only admin can reassign tickets to other agents
            if user.is_agent and data.assigned_to_id != user.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Agents cannot reassign tickets to other users.",
                )
            updates.append(f"assigned_to_id = ${idx}")
            params.append(data.assigned_to_id)
            idx += 1

        if not updates:
            return current_ticket

        updates.append("updated_at = NOW()")
        update_str = ", ".join(updates)

        query = f"""
            UPDATE tickets
            SET {update_str}
            WHERE id = ${idx}
            RETURNING id, title, description, customer_email, status, priority,
                      category, ai_summary, manual_triage_required, created_by_id,
                      assigned_to_id, created_at, updated_at
        """
        params.append(ticket_id)

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, *params)

            # Auto-cancel pending reminders when ticket is marked as resolved or closed
            if data.status in [TicketStatus.RESOLVED, TicketStatus.CLOSED]:
                cancel_query = """
                    UPDATE reminders
                    SET status = 'cancelled', updated_at = NOW()
                    WHERE ticket_id = $1 AND status = 'pending'
                """
                await conn.execute(cancel_query, ticket_id)
                logger.info("Auto-cancelled pending reminders for ticket %s", ticket_id)

        return TicketResponse(**dict(row))

    async def create_note(self, ticket_id: UUID, data: NoteCreate, user: UserResponse) -> NoteResponse:
        """Adds an internal note to a ticket."""
        # Enforce ticket access permission
        await self.get_ticket(ticket_id, user)

        note_id = uuid6.uuid7()
        query = """
            INSERT INTO ticket_notes (id, ticket_id, author_id, content, created_at)
            VALUES ($1, $2, $3, $4, NOW())
            RETURNING id, ticket_id, author_id, content, created_at
        """
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(query, note_id, ticket_id, user.id, data.content)

        return NoteResponse(
            id=row["id"],
            ticket_id=row["ticket_id"],
            author_id=row["author_id"],
            content=row["content"],
            created_at=row["created_at"],
            author_name=user.full_name,
        )

    async def list_notes(self, ticket_id: UUID, user: UserResponse) -> List[NoteResponse]:
        """Lists all internal notes for a ticket."""
        # Enforce ticket access permission
        await self.get_ticket(ticket_id, user)

        query = """
            SELECT n.id, n.ticket_id, n.author_id, n.content, n.created_at, u.full_name as author_name
            FROM ticket_notes n
            JOIN users u ON n.author_id = u.id
            WHERE n.ticket_id = $1
            ORDER BY n.created_at ASC
        """
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(query, ticket_id)

        return [NoteResponse(**dict(r)) for r in rows]
