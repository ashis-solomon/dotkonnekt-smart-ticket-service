"""FastAPI route dependencies for database access, authentication, and role checking."""

from typing import Callable, List, Optional
import asyncpg
from fastapi import Depends, HTTPException, Request, status
from app.config import Settings, get_settings
from app.schemas.user import UserResponse, UserRole
from app.services.auth_service import AuthService


def get_db_pool(request: Request) -> asyncpg.Pool:
    """Retrieves the asyncpg database pool from the application state."""
    pool: Optional[asyncpg.Pool] = getattr(request.app.state, "db_pool", None)
    if not pool:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database connection pool is not available.",
        )
    return pool


def get_auth_service(
    settings: Settings = Depends(get_settings),
    pool: asyncpg.Pool = Depends(get_db_pool),
) -> AuthService:
    """Provides an AuthService instance."""
    return AuthService(settings=settings, pool=pool)


def get_current_user(request: Request) -> UserResponse:
    """Retrieves the authenticated user already validated by RoleMiddleware."""
    user: Optional[UserResponse] = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication credentials were not provided.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_roles(allowed_roles: List[UserRole]) -> Callable:
    """Dependency factory verifying that the authenticated user has one of the allowed roles."""
    def role_checker(current_user: UserResponse = Depends(get_current_user)) -> UserResponse:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have permission to access this resource.",
            )
        return current_user

    return role_checker


def require_admin(current_user: UserResponse = Depends(get_current_user)) -> UserResponse:
    """Convenience dependency requiring Admin privileges."""
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required.",
        )
    return current_user


def require_admin_or_agent(current_user: UserResponse = Depends(get_current_user)) -> UserResponse:
    """Convenience dependency requiring either Admin or Agent role."""
    if not (current_user.is_admin or current_user.is_agent):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Valid role privileges required.",
        )
    return current_user
