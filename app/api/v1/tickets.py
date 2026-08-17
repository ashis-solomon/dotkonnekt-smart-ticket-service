"""Ticket management endpoints with trigram fuzzy search, filtering, and role enforcement."""

import math
from typing import Optional
from uuid import UUID
import asyncpg
from fastapi import APIRouter, Depends, Query, Request, status

from app.api.deps import get_current_user, get_db_pool
from app.schemas.common import (
    ApiResponse,
    MetaResponse,
    PaginatedMetaResponse,
    PaginatedResponse,
    PaginationMeta,
    generate_request_id,
)
from app.schemas.ticket import (
    TicketCategory,
    TicketCreate,
    TicketPriority,
    TicketResponse,
    TicketStatus,
    TicketUpdate,
)
from app.schemas.user import UserResponse
from app.services.ticket_service import TicketService

router = APIRouter(prefix="/tickets", tags=["Tickets"])


@router.post(
    "",
    response_model=ApiResponse[TicketResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create a new support ticket",
)
async def create_ticket(
    request: Request,
    data: TicketCreate,
    current_user: UserResponse = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> ApiResponse[TicketResponse]:
    request_id = getattr(request.state, "request_id", generate_request_id())
    service = TicketService(pool)
    ticket = await service.create_ticket(data, current_user)
    return ApiResponse(
        data=ticket,
        meta=MetaResponse(requestId=request_id),
    )


@router.get(
    "",
    response_model=PaginatedResponse[TicketResponse],
    status_code=status.HTTP_200_OK,
    summary="List tickets with trigram search, compound filters, and pagination",
)
async def list_tickets(
    request: Request,
    q: Optional[str] = Query(None, description="Fuzzy keyword search over title & description"),
    status: Optional[TicketStatus] = Query(None, description="Filter by ticket status"),
    priority: Optional[TicketPriority] = Query(None, description="Filter by ticket priority"),
    category: Optional[TicketCategory] = Query(None, description="Filter by ticket category"),
    assigned_to_id: Optional[UUID] = Query(None, description="Filter by assigned user ID"),
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    current_user: UserResponse = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> PaginatedResponse[TicketResponse]:
    request_id = getattr(request.state, "request_id", generate_request_id())
    service = TicketService(pool)
    tickets, total_records = await service.list_tickets(
        user=current_user,
        q=q,
        status_filter=status,
        priority_filter=priority,
        category_filter=category,
        assigned_to_id=assigned_to_id,
        page=page,
        page_size=page_size,
    )

    total_pages = math.ceil(total_records / page_size) if total_records > 0 else 1
    has_next = page < total_pages
    has_prev = page > 1

    return PaginatedResponse(
        data=tickets,
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


@router.get(
    "/{ticket_id}",
    response_model=ApiResponse[TicketResponse],
    status_code=status.HTTP_200_OK,
    summary="Get ticket details by ID",
)
async def get_ticket(
    request: Request,
    ticket_id: UUID,
    current_user: UserResponse = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> ApiResponse[TicketResponse]:
    request_id = getattr(request.state, "request_id", generate_request_id())
    service = TicketService(pool)
    ticket = await service.get_ticket(ticket_id, current_user)
    return ApiResponse(
        data=ticket,
        meta=MetaResponse(requestId=request_id),
    )


@router.patch(
    "/{ticket_id}",
    response_model=ApiResponse[TicketResponse],
    status_code=status.HTTP_200_OK,
    summary="Update ticket status, priority, category, or assignee",
)
async def update_ticket(
    request: Request,
    ticket_id: UUID,
    data: TicketUpdate,
    current_user: UserResponse = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> ApiResponse[TicketResponse]:
    request_id = getattr(request.state, "request_id", generate_request_id())
    service = TicketService(pool)
    ticket = await service.update_ticket(ticket_id, data, current_user)
    return ApiResponse(
        data=ticket,
        meta=MetaResponse(requestId=request_id),
    )
