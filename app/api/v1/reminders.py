"""Ticket follow-up reminder endpoints."""

from typing import List
from uuid import UUID
import asyncpg
from fastapi import APIRouter, Depends, Request, status

from app.api.deps import get_current_user, get_db_pool
from app.schemas.common import ApiResponse, MetaResponse, generate_request_id
from app.schemas.reminder import ReminderCreate, ReminderResponse
from app.schemas.user import UserResponse
from app.services.reminder_service import ReminderService

router = APIRouter(prefix="/tickets/{ticket_id}/reminders", tags=["Reminders"])


@router.post(
    "",
    response_model=ApiResponse[ReminderResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Schedule a follow-up reminder on a ticket",
)
async def create_reminder(
    request: Request,
    ticket_id: UUID,
    data: ReminderCreate,
    current_user: UserResponse = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> ApiResponse[ReminderResponse]:
    request_id = getattr(request.state, "request_id", generate_request_id())
    service = ReminderService(pool)
    reminder = await service.create_reminder(ticket_id, data, current_user)
    return ApiResponse(
        data=reminder,
        meta=MetaResponse(requestId=request_id),
    )


@router.get(
    "",
    response_model=ApiResponse[List[ReminderResponse]],
    status_code=status.HTTP_200_OK,
    summary="List all reminders scheduled on a ticket",
)
async def list_reminders(
    request: Request,
    ticket_id: UUID,
    current_user: UserResponse = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> ApiResponse[List[ReminderResponse]]:
    request_id = getattr(request.state, "request_id", generate_request_id())
    service = ReminderService(pool)
    reminders = await service.list_reminders(ticket_id, current_user)
    return ApiResponse(
        data=reminders,
        meta=MetaResponse(requestId=request_id),
    )
