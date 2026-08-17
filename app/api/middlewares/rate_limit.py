"""Rate limiting integration using SlowAPI."""

from datetime import datetime, timezone
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.schemas.common import generate_request_id

# Central SlowAPI Limiter instance
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["120/minute"],
    headers_enabled=True,
)


def custom_rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """Standardized JSON response envelope for 429 Too Many Requests."""
    request_id = getattr(request.state, "request_id", generate_request_id())
    retry_after = getattr(exc, "retry_after", 60)
    return JSONResponse(
        status_code=429,
        headers={
            "X-Request-ID": request_id,
            "Retry-After": str(retry_after),
        },
        content={
            "success": False,
            "error": {
                "code": "RATE_LIMIT_EXCEEDED",
                "message": f"Too many requests: {exc.detail}",
                "details": [],
            },
            "meta": {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "requestId": request_id,
            },
        },
    )
