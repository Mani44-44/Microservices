"""
TicketFlow API Gateway — application entry point.

Responsibilities:
  - Starts and tears down shared resources (Redis, httpx client).
  - Registers CORS, correlation-ID/logging middleware.
  - Mounts route modules for auth, events, and bookings.
  - Exposes a top-level /api/v1/health liveness probe.
"""

import time
import uuid
from contextlib import asynccontextmanager

import httpx
import redis.asyncio as aioredis
import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response

from app.config import get_settings
from app.middleware.rate_limit import RateLimiter
from app.routes import auth, bookings, events

log = structlog.get_logger()
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Create shared async resources at startup; cleanly close them on shutdown.

    Resources stored on ``app.state`` so route handlers can access them via
    ``request.app.state``.
    """
    log.info("gateway_starting", redis_url=settings.redis_url)

    app.state.redis = aioredis.from_url(
        settings.redis_url,
        decode_responses=False,  # Lua scripts return raw bytes
    )
    app.state.http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=5.0),
        follow_redirects=False,
    )
    app.state.rate_limiter = RateLimiter(
        redis=app.state.redis,
        per_user_limit=settings.rate_limit_per_user_per_minute,
        per_ip_limit=settings.rate_limit_per_ip_per_minute,
    )

    log.info("gateway_started", port=8000, service=settings.service_name)
    yield

    await app.state.redis.aclose()
    await app.state.http_client.aclose()
    log.info("gateway_stopped")


app = FastAPI(
    title="TicketFlow API Gateway",
    description="Single ingress point for all TicketFlow microservices.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/v1/docs",
    redoc_url="/api/v1/redoc",
)

from libs.observability import add_security_headers, instrument_metrics, instrument_tracing

instrument_metrics(app, settings.service_name)
add_security_headers(app)
instrument_tracing(app, settings.service_name)

# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Correlation-ID + structured request logging middleware
# ---------------------------------------------------------------------------
@app.middleware("http")
async def correlation_logging_middleware(request: Request, call_next):
    """
    Attach a correlation ID to every request and log the full request/response
    cycle with method, path, status, and duration.
    """
    correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
    request.state.correlation_id = correlation_id

    client_ip = request.client.host if request.client else "unknown"
    start = time.perf_counter()

    response: Response = await call_next(request)

    duration_ms = round((time.perf_counter() - start) * 1000, 2)
    response.headers["X-Correlation-ID"] = correlation_id

    log.info(
        "gateway_request",
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        duration_ms=duration_ms,
        correlation_id=correlation_id,
        client_ip=client_ip,
    )
    return response


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------
app.include_router(auth.router)
app.include_router(events.router)
app.include_router(bookings.router)


# ---------------------------------------------------------------------------
# Top-level health probe
# ---------------------------------------------------------------------------
@app.get("/api/v1/health", tags=["health"])
async def health() -> dict:
    """
    Gateway liveness probe.

    Returns immediately without touching Redis or any downstream service.
    """
    return {"status": "healthy", "service": "api-gateway", "version": "1.0.0"}


# ---------------------------------------------------------------------------
# HTTPException handler — unwrap {"error": {...}} details into a top-level
# envelope instead of FastAPI's default {"detail": ...} wrapping.
# ---------------------------------------------------------------------------
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    correlation_id = getattr(request.state, "correlation_id", str(uuid.uuid4()))
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        content = exc.detail
        content["error"].setdefault("correlation_id", correlation_id)
    else:
        content = {
            "error": {
                "code": "HTTP_ERROR",
                "message": str(exc.detail),
                "details": {},
                "correlation_id": correlation_id,
            }
        }
    return JSONResponse(status_code=exc.status_code, content=content)


# ---------------------------------------------------------------------------
# Global exception handler — convert unhandled errors to standard envelope
# ---------------------------------------------------------------------------
@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    correlation_id = getattr(request.state, "correlation_id", str(uuid.uuid4()))
    log.exception(
        "unhandled_exception",
        path=request.url.path,
        correlation_id=correlation_id,
        error=str(exc),
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "An unexpected error occurred",
                "details": {},
                "correlation_id": correlation_id,
            }
        },
    )
