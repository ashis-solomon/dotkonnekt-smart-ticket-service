"""Middleware to attach correlation UUIDv7 Request ID to request, response, and contextvar."""

from contextvars import ContextVar
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from app.schemas.common import generate_request_id

# Context variable accessible by any logger across the execution thread
request_id_ctx_var: ContextVar[str] = ContextVar("request_id", default="-")


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        client_req_id = request.headers.get("X-Request-ID")
        request_id = client_req_id if (client_req_id and client_req_id.startswith("req_")) else generate_request_id()

        request.state.request_id = request_id
        token = request_id_ctx_var.set(request_id)

        try:
            response: Response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            request_id_ctx_var.reset(token)
