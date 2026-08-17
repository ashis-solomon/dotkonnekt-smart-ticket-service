"""Internal notes endpoints for support tickets."""

from typing import List
from uuid import UUID
import asyncpg
from fastapi import APIRouter, Depends, Request, status

from app.api.deps import get_current_user, get_db_pool
from app.schemas.common import ApiResponse, MetaResponse, generate_request_id
from app.schemas.note import NoteCreate, NoteResponse
from app.schemas.user import UserResponse
from app.services.ticket_service import TicketService

router = APIRouter(prefix="/tickets/{ticket_id}/notes", tags=["Ticket Notes"])


@router.post(
    "",
    response_model=ApiResponse[NoteResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Add an internal note to a ticket",
)
async def create_note(
    request: Request,
    ticket_id: UUID,
    data: NoteCreate,
    current_user: UserResponse = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> ApiResponse[NoteResponse]:
    request_id = getattr(request.state, "request_id", generate_request_id())
    service = TicketService(pool)
    note = await service.create_note(ticket_id, data, current_user)
    return ApiResponse(
        data=note,
        meta=MetaResponse(requestId=request_id),
    )


@router.get(
    "",
    response_model=ApiResponse[List[NoteResponse]],
    status_code=status.HTTP_200_OK,
    summary="List all internal notes for a ticket",
)
async def list_notes(
    request: Request,
    ticket_id: UUID,
    current_user: UserResponse = Depends(get_current_user),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> ApiResponse[List[NoteResponse]]:
    request_id = getattr(request.state, "request_id", generate_request_id())
    service = TicketService(pool)
    notes = await service.list_notes(ticket_id, current_user)
    return ApiResponse(
        data=notes,
        meta=MetaResponse(requestId=request_id),
    )
