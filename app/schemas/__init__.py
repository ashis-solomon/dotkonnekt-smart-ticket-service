"""Pydantic schemas export."""

from app.schemas.common import (
    ApiResponse,
    ErrorResponse,
    ErrorContent,
    MetaResponse,
    PaginationMeta,
    PaginatedResponse,
    PaginatedMetaResponse,
    generate_request_id,
)
from app.schemas.user import UserBase, UserResponse, UserRole
from app.schemas.auth import (
    UserRegister,
    UserLogin,
    TokenResponse,
    TokenRefreshRequest,
    TokenRefreshResponse,
    LogoutRequest,
)
from app.schemas.ticket import (
    TicketStatus,
    TicketPriority,
    TicketCategory,
    TicketCreate,
    TicketUpdate,
    TicketResponse,
)
from app.schemas.note import NoteCreate, NoteResponse
from app.schemas.reminder import (
    ReminderStatus,
    ReminderCreate,
    ReminderResponse,
    NotificationResponse,
)

__all__ = [
    "ApiResponse",
    "ErrorResponse",
    "ErrorContent",
    "MetaResponse",
    "PaginationMeta",
    "PaginatedResponse",
    "PaginatedMetaResponse",
    "generate_request_id",
    "UserBase",
    "UserResponse",
    "UserRole",
    "UserRegister",
    "UserLogin",
    "TokenResponse",
    "TokenRefreshRequest",
    "TokenRefreshResponse",
    "LogoutRequest",
    "TicketStatus",
    "TicketPriority",
    "TicketCategory",
    "TicketCreate",
    "TicketUpdate",
    "TicketResponse",
    "NoteCreate",
    "NoteResponse",
    "ReminderStatus",
    "ReminderCreate",
    "ReminderResponse",
    "NotificationResponse",
]
