"""Role-checking and authentication middleware."""

from datetime import datetime, timezone
import logging
from typing import Set
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
import jwt

from app.config import get_settings
from app.schemas.common import generate_request_id
from app.schemas.user import UserResponse, UserRole

logger = logging.getLogger("app.api.role_middleware")

PUBLIC_PATHS: Set[str] = {
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/v1/auth/login",
    "/api/v1/auth/register",
    "/api/v1/auth/refresh",
    "/api/v1/auth/logout",
}


def _error_response(request: Request, status_code: int, code: str, message: str) -> JSONResponse:
    request_id = getattr(request.state, "request_id", generate_request_id())
    headers = {"X-Request-ID": request_id}
    if status_code == 401:
        headers["WWW-Authenticate"] = "Bearer"

    return JSONResponse(
        status_code=status_code,
        headers=headers,
        content={
            "success": False,
            "error": {"code": code, "message": message, "details": []},
            "meta": {"timestamp": datetime.now(timezone.utc).isoformat(), "requestId": request_id},
        },
    )


class RoleMiddleware(BaseHTTPMiddleware):
    """
    Middleware that intercepts incoming requests to:
    1. Skip public endpoints.
    2. Extract & validate Bearer JWT token.
    3. Validate user role ('admin' or 'agent').
    4. Attach authenticated user object to request.state.user.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path

        if path in PUBLIC_PATHS or path.startswith(("/docs", "/redoc", "/openapi")):
            request.state.user = None
            return await call_next(request)

        auth_header = request.headers.get("Authorization")
        if not auth_header or not auth_header.startswith("Bearer "):
            return _error_response(request, 401, "UNAUTHORIZED", "Authentication credentials were not provided.")

        token = auth_header.split(" ", 1)[1].strip()
        if not token:
            return _error_response(request, 401, "UNAUTHORIZED", "Authentication credentials were not provided.")

        settings = get_settings()

        try:
            payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        except jwt.ExpiredSignatureError:
            return _error_response(request, 401, "UNAUTHORIZED", "Token has expired.")
        except jwt.PyJWTError as e:
            return _error_response(request, 401, "UNAUTHORIZED", f"Invalid token: {str(e)}")

        if payload.get("type") != "access":
            return _error_response(request, 401, "UNAUTHORIZED", "Invalid token type.")

        user_id = payload.get("sub")
        role_str = payload.get("role")
        if not user_id or not role_str:
            return _error_response(request, 401, "UNAUTHORIZED", "Invalid token payload.")

        # Middleware Role Validation (Admin or Agent)
        if role_str not in [UserRole.ADMIN.value, UserRole.AGENT.value]:
            return _error_response(request, 403, "FORBIDDEN", f"Invalid user role: {role_str}")

        pool = getattr(request.app.state, "db_pool", None)
        if pool:
            query = "SELECT id, email, full_name, role, is_active, created_at, updated_at FROM users WHERE id = $1"
            async with pool.acquire() as conn:
                row = await conn.fetchrow(query, user_id)

            if not row:
                return _error_response(request, 401, "UNAUTHORIZED", "User not found.")

            if not row["is_active"]:
                return _error_response(request, 403, "FORBIDDEN", "User account is deactivated.")

            user = UserResponse(**dict(row))
        else:
            user = UserResponse(
                id=user_id,
                email=payload.get("email", ""),
                full_name=payload.get("name", "User"),
                role=UserRole(role_str),
                is_active=True,
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )

        request.state.user = user
        request.state.user_role = user.role
        return await call_next(request)
