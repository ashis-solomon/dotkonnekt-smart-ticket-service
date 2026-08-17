from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator


class ReminderStatus(str, Enum):
    PENDING = "pending"
    FIRED = "fired"
    CANCELLED = "cancelled"


class ReminderCreate(BaseModel):
    scheduled_for: datetime
    message: str = Field(..., min_length=1, max_length=1000)

    @field_validator("scheduled_for")
    @classmethod
    def validate_future_time(cls, v: datetime) -> datetime:
        now = datetime.now(timezone.utc)
        v_utc = v if v.tzinfo else v.replace(tzinfo=timezone.utc)
        if v_utc <= now:
            raise ValueError("Reminder scheduled_for time must be in the future.")
        return v


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
