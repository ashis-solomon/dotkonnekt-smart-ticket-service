"""Authentication request and response schemas."""

from typing import Optional
from pydantic import BaseModel, EmailStr, Field
from app.config import get_settings
from app.schemas.user import UserResponse, UserRole


class UserRegister(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, description="Password must be at least 8 characters long")
    full_name: str = Field(..., min_length=2, max_length=100)
    role: Optional[UserRole] = UserRole.AGENT


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = Field(default_factory=lambda: get_settings().access_token_expire_seconds)
    user: UserResponse


class TokenRefreshRequest(BaseModel):
    refresh_token: str


class TokenRefreshResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = Field(default_factory=lambda: get_settings().access_token_expire_seconds)


class LogoutRequest(BaseModel):
    refresh_token: str
