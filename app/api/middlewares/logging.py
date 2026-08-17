"""Structured logging filter and access logging middleware with latency."""

import logging
import time
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from app.api.middlewares.request_id import request_id_ctx_var

logger = logging.getLogger("app.api.access")


class RequestIdLogFilter(logging.Filter):
    """Logging filter that injects the current request ID into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx_var.get("-")
        return True


class StructuredLoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        start_time = time.perf_counter()
        client_ip = request.client.host if request.client else "unknown"

        try:
            response: Response = await call_next(request)
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            logger.info(
                "%s %s %d - %s - %.2fms",
                request.method,
                request.url.path,
                response.status_code,
                client_ip,
                duration_ms,
            )
            return response
        except Exception as exc:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            logger.error(
                "%s %s 500 - %s - %.2fms - error: %s",
                request.method,
                request.url.path,
                client_ip,
                duration_ms,
                str(exc),
                exc_info=True,
            )
            raise exc
