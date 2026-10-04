import asyncio
import uuid
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
import structlog
from aiokafka import AIOKafkaProducer
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.database import async_session_factory, get_db, init_db
from app.lock_manager import get_lock_manager
from app.outbox_relay import outbox_relay_loop
from app.routes import bookings, internal, users

# Configure structlog for JSON output
structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)

log = structlog.get_logger()
settings = get_settings()


async def _expiry_loop(app: FastAPI) -> None:
    """Background task: force-release expired seat locks every N seconds."""
    import app.booking_service as svc

    while True:
        await asyncio.sleep(settings.expiry_check_interval_seconds)
        try:
            async with async_session_factory() as db:
                lock_manager = get_lock_manager(app.state.redis)
                await svc.expire_pending_bookings(db, lock_manager)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("expiry_loop_error", error=str(exc))


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("booking_service_startup", service=settings.service_name)
    await init_db()
    app.state.redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    expiry_task = asyncio.create_task(_expiry_loop(app))

    producer = AIOKafkaProducer(bootstrap_servers=settings.kafka_brokers)
    await producer.start()
    app.state.kafka_producer = producer
    relay_task = asyncio.create_task(outbox_relay_loop(producer))

    log.info("database_initialised")
    yield

    expiry_task.cancel()
    relay_task.cancel()
    for task in (expiry_task, relay_task):
        try:
            await task
        except asyncio.CancelledError:
            pass
    await producer.stop()
    await app.state.redis.aclose()
    log.info("booking_service_shutdown", service=settings.service_name)


app = FastAPI(
    title="TicketFlow Booking Service",
    description="Core seat-locking, booking confirmation, and release flows for TicketFlow.",
    version="1.0.0",
    lifespan=lifespan,
)

from libs.observability import add_security_headers, instrument_metrics, instrument_tracing
from libs.security import require_internal_secret

instrument_metrics(app, settings.service_name)
add_security_headers(app)
instrument_tracing(app, settings.service_name)
require_internal_secret(app)


# ---------------------------------------------------------------------------
# Correlation-ID middleware
# ---------------------------------------------------------------------------


@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
    request.state.correlation_id = correlation_id

    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(
        correlation_id=correlation_id,
        path=request.url.path,
        method=request.method,
    )

    response = await call_next(request)
    response.headers["X-Correlation-ID"] = correlation_id
    return response


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
# Global exception handler
# ---------------------------------------------------------------------------


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    correlation_id = getattr(request.state, "correlation_id", str(uuid.uuid4()))
    log.error(
        "unhandled_exception",
        error=str(exc),
        correlation_id=correlation_id,
        exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "INTERNAL_SERVER_ERROR",
                "message": "An unexpected error occurred",
                "details": {},
                "correlation_id": correlation_id,
            }
        },
    )


# ---------------------------------------------------------------------------
# Routers
# ---------------------------------------------------------------------------

app.include_router(bookings.router)
app.include_router(users.router)
app.include_router(internal.router)


# ---------------------------------------------------------------------------
# Health / root
# ---------------------------------------------------------------------------


@app.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok", "service": settings.service_name}


@app.get("/", include_in_schema=False)
async def root():
    return {"service": settings.service_name, "status": "running"}
