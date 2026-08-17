"""Reminder and Notification schemas."""

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class ReminderStatus(str, Enum):
    PENDING = "pending"
    FIRED = "fired"
    CANCELLED = "cancelled"


class ReminderCreate(BaseModel):
    scheduled_for: datetime
    message: str = Field(..., min_length=1, max_length=1000)


class ReminderResponse(BaseModel):
    id: UUID
    ticket_id: UUID
    created_by_id: UUID
    scheduled_for: datetime
    message: str
    status: ReminderStatus
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class NotificationResponse(BaseModel):
    id: UUID
    reminder_id: Optional[UUID] = None
    user_id: UUID
    message: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
