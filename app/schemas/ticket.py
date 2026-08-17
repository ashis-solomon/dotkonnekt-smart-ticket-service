"""Ticket schemas, enums, and query filter DTOs."""

from datetime import datetime
from enum import Enum
from typing import List, Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from app.schemas.note import NoteResponse


class TicketStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"


class TicketPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class TicketCategory(str, Enum):
    BILLING = "billing"
    TECHNICAL = "technical"
    ACCOUNT = "account"
    OTHER = "other"


class TicketCreate(BaseModel):
    title: str = Field(..., min_length=3, max_length=255)
    description: str = Field(..., min_length=5)
    customer_email: EmailStr
    priority: Optional[TicketPriority] = None
    assignee_email: Optional[EmailStr] = None


class TicketUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=3, max_length=255)
    description: Optional[str] = Field(None, min_length=5)
    status: Optional[TicketStatus] = None
    priority: Optional[TicketPriority] = None
    category: Optional[TicketCategory] = None
    assignee_email: Optional[EmailStr] = None


class TicketResponse(BaseModel):
    id: UUID
    title: str
    description: str
    customer_email: str
    status: TicketStatus
    priority: TicketPriority
    category: Optional[TicketCategory] = None
    ai_summary: Optional[str] = None
    manual_triage_required: bool = False
    created_by_id: UUID
    assigned_to_id: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime
    notes: List[NoteResponse] = Field(default_factory=list, description="Internal notes attached to the ticket")

    model_config = ConfigDict(from_attributes=True)
