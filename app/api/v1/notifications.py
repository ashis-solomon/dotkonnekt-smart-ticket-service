import math
from typing import List, Optional
from uuid import UUID
import asyncpg
from fastapi import APIRouter, Depends, Query, Request, status

from app.api.deps import get_current_user, get_db_pool
from app.schemas.common import (
    PaginatedMetaResponse,
    PaginatedResponse,
    PaginationMeta,
    generate_request_id,
)
from app.schemas.reminder import NotificationResponse
from app.schemas.user import UserResponse
from app.services.reminder_service import ReminderService

router = APIRouter(prefix="/notifications", tags=["Notifications"])


@router.get(
    "",
    response_model=PaginatedResponse[NotificationResponse],
    status_code=status.HTTP_200_OK,
    summary="List paginated notifications (Admins view all, Agents view their own)",
)
async def list_notifications(
    request: Request,
    user_id: Optional[UUID] = Query(None, description="Filter notifications by user ID (Admin only)"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    current_user: UserResponse = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> PaginatedResponse[NotificationResponse]:
    request_id = getattr(request.state, "request_id", generate_request_id())
    service = ReminderService(pool)
    notifications, total_records = await service.list_notifications(
        user=current_user,
        target_user_id=user_id,
        page=page,
        page_size=page_size,
    )

    total_pages = math.ceil(total_records / page_size) if total_records > 0 else 1
    has_next = page < total_pages
    has_prev = page > 1

    return PaginatedResponse(
        data=notifications,
        meta=PaginatedMetaResponse(
            requestId=request_id,
            pagination=PaginationMeta(
                page=page,
                pageSize=page_size,
                totalRecords=total_records,
                totalPages=total_pages,
                hasNext=has_next,
                hasPrev=has_prev,
            ),
        ),
    )
