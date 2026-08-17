"""Rate limiting middleware for brute-force protection."""

import asyncio
from collections import defaultdict
from datetime import datetime, timezone
import time
from typing import Dict, List
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from app.config import get_settings
from app.schemas.common import generate_request_id


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self.settings = get_settings()
        # IP -> list of timestamps
        self._login_attempts: Dict[str, List[float]] = defaultdict(list)
        self._lock = asyncio.Lock()

    async def dispatch(self, request: Request, call_next) -> Response:
        # Only rate-limit POST /api/v1/auth/login or /auth/login
        if request.method == "POST" and request.url.path.endswith("/auth/login"):
            client_ip = request.client.host if request.client else "unknown"
            now = time.time()
            window = self.settings.RATE_LIMIT_LOGIN_WINDOW_SECONDS
            max_requests = self.settings.RATE_LIMIT_LOGIN_MAX_REQUESTS

            async with self._lock:
                # Filter out old attempts outside the window
                self._login_attempts[client_ip] = [
                    t for t in self._login_attempts[client_ip] if now - t < window
                ]

                if len(self._login_attempts[client_ip]) >= max_requests:
                    request_id = getattr(request.state, "request_id", generate_request_id())
                    return JSONResponse(
                        status_code=429,
                        headers={"X-Request-ID": request_id, "Retry-After": str(window)},
                        content={
                            "success": False,
                            "error": {
                                "code": "RATE_LIMIT_EXCEEDED",
                                "message": f"Too many login attempts. Please try again after {window} seconds.",
                                "details": [],
                            },
                            "meta": {
                                "timestamp": datetime.now(timezone.utc).isoformat(),
                                "requestId": request_id,
                            },
                        },
                    )

                self._login_attempts[client_ip].append(now)

        return await call_next(request)
