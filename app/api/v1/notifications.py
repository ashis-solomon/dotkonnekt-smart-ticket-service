"""User notifications endpoints."""

from typing import List
import asyncpg
from fastapi import APIRouter, Depends, Request, status

from app.api.deps import get_current_user, get_db_pool
from app.schemas.common import ApiResponse, MetaResponse, generate_request_id
from app.schemas.reminder import NotificationResponse
from app.schemas.user import UserResponse
from app.services.reminder_service import ReminderService

router = APIRouter(prefix="/notifications", tags=["Notifications"])


@router.get(
    "",
    response_model=ApiResponse[List[NotificationResponse]],
    status_code=status.HTTP_200_OK,
    summary="List notifications for current user",
)
async def list_notifications(
    request: Request,
    current_user: UserResponse = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> ApiResponse[List[NotificationResponse]]:
    request_id = getattr(request.state, "request_id", generate_request_id())
    service = ReminderService(pool)
    notifications = await service.list_notifications(current_user)
    return ApiResponse(
        data=notifications,
        meta=MetaResponse(requestId=request_id),
    )
