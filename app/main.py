"""FastAPI application entrypoint, lifespan manager, middlewares, and standardized exception handlers."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import logging
from typing import Any, Dict, List
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.middlewares.logging import RequestIdLogFilter, StructuredLoggingMiddleware
from app.api.middlewares.rate_limit import RateLimitMiddleware
from app.api.middlewares.request_id import RequestIdMiddleware
from app.api.middlewares.role import RoleMiddleware
from app.api.v1.api import api_v1_router
from app.config import get_settings
from app.database import close_db_pool, create_db_pool, init_db
from app.schemas.common import generate_request_id

# Configure root logger with Request ID injection
_log_format = "%(asctime)s [%(levelname)s] [%(request_id)s] %(name)s: %(message)s"
_formatter = logging.Formatter(_log_format)
_handler = logging.StreamHandler()
_handler.setFormatter(_formatter)
_handler.addFilter(RequestIdLogFilter())

logging.root.handlers = [_handler]
logging.root.setLevel(logging.INFO)
logger = logging.getLogger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager handling database connection pool initialization and teardown."""
    settings = get_settings()
    logger.info("Starting up Smart Ticket Support System backend (env=%s)...", settings.ENVIRONMENT)

    try:
        pool = await create_db_pool(settings)
        await init_db(pool)
        app.state.db_pool = pool
        logger.info("Database connection pool ready and schema verified.")
    except Exception as exc:
        logger.error("Failed to initialize database pool on startup: %s", str(exc))
        app.state.db_pool = None

    yield

    logger.info("Shutting down application...")
    if getattr(app.state, "db_pool", None):
        await close_db_pool(app.state.db_pool)


def _build_error_response(request: Request, status_code: int, code: str, message: str, details: List[Any] = None) -> JSONResponse:
    """Helper to return consistent JSON error envelopes."""
    request_id = getattr(request.state, "request_id", generate_request_id())
    return JSONResponse(
        status_code=status_code,
        headers={"X-Request-ID": request_id},
        content={
            "success": False,
            "error": {"code": code, "message": message, "details": details or []},
            "meta": {"timestamp": datetime.now(timezone.utc).isoformat(), "requestId": request_id},
        },
    )


def create_application() -> FastAPI:
    """Builds and configures the FastAPI application instance."""
    app = FastAPI(
        title="Smart Ticket Support System API",
        description="High-performance backend with asyncpg native queries, Celery background tasks, and LLM auto-triage.",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
    app.add_middleware(RoleMiddleware)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(StructuredLoggingMiddleware)
    app.add_middleware(RequestIdMiddleware)

    # Standardized Exception Handlers
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        status_codes = {
            400: "BAD_REQUEST", 401: "UNAUTHORIZED", 403: "FORBIDDEN",
            404: "NOT_FOUND", 409: "CONFLICT", 422: "VALIDATION_ERROR", 429: "RATE_LIMIT_EXCEEDED",
        }
        return _build_error_response(request, exc.status_code, status_codes.get(exc.status_code, "ERROR"), str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        details = [{"field": " -> ".join(str(l) for l in err.get("loc", [])), "issue": err.get("msg", "Invalid value")} for err in exc.errors()]
        return _build_error_response(request, 422, "VALIDATION_ERROR", "Input validation failed.", details)

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception):
        logger.error("Unhandled exception: %s", str(exc), exc_info=True)
        return _build_error_response(request, 500, "INTERNAL_SERVER_ERROR", "An unexpected error occurred.")

    @app.get("/health", tags=["Health"], summary="System health check")
    async def health_check(request: Request) -> Dict[str, Any]:
        request_id = getattr(request.state, "request_id", generate_request_id())
        pool = getattr(request.app.state, "db_pool", None)
        db_healthy = False
        if pool:
            try:
                async with pool.acquire() as conn:
                    db_healthy = (await conn.fetchval("SELECT 1")) == 1
            except Exception:
                db_healthy = False

        return {
            "success": True,
            "data": {
                "status": "healthy" if db_healthy else "degraded",
                "database": "connected" if db_healthy else "disconnected",
                "llm_provider": get_settings().LLM_PROVIDER,
            },
            "meta": {"timestamp": datetime.now(timezone.utc).isoformat(), "requestId": request_id},
        }

    app.include_router(api_v1_router)
    return app


app = create_application()
