"""Standard API response envelopes, metadata models, and pagination helpers."""

from datetime import datetime, timezone
from typing import Any, Generic, List, Optional, TypeVar
from pydantic import BaseModel, Field
import uuid6

T = TypeVar("T")


def generate_request_id() -> str:
    """Generates a UUIDv7 based request ID with prefix req_."""
    return f"req_{uuid6.uuid7()}"


class MetaResponse(BaseModel):
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    requestId: str


class PaginationMeta(BaseModel):
    page: int
    pageSize: int
    totalRecords: int
    totalPages: int
    hasNext: bool
    hasPrev: bool


class PaginatedMetaResponse(MetaResponse):
    pagination: PaginationMeta


class ApiResponse(BaseModel, Generic[T]):
    success: bool = True
    data: T
    meta: MetaResponse


class PaginatedResponse(BaseModel, Generic[T]):
    success: bool = True
    data: List[T]
    meta: PaginatedMetaResponse


class ErrorContent(BaseModel):
    code: str
    message: str
    details: List[Any] = []


class ErrorResponse(BaseModel):
    success: bool = False
    error: ErrorContent
    meta: MetaResponse
