import asyncio
import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.consumer import consume_loop
from app.database import init_db

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(20),
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
)

log = structlog.get_logger()
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("notification_service_startup", service=settings.service_name)
    await init_db()
    consumer_task = asyncio.create_task(consume_loop())
    yield
    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass
    log.info("notification_service_shutdown", service=settings.service_name)


app = FastAPI(
    title="TicketFlow Notification Service",
    description="Consumes booking.events and sends (stub) notifications.",
    version="1.0.0",
    lifespan=lifespan,
)

from libs.observability import add_security_headers, instrument_metrics, instrument_tracing
from libs.security import require_internal_secret

instrument_metrics(app, settings.service_name)
add_security_headers(app)
instrument_tracing(app, settings.service_name)
require_internal_secret(app)


@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
    request.state.correlation_id = correlation_id
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(correlation_id=correlation_id, path=request.url.path)
    response = await call_next(request)
    response.headers["X-Correlation-ID"] = correlation_id
    return response


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


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    correlation_id = getattr(request.state, "correlation_id", str(uuid.uuid4()))
    log.error("unhandled_exception", error=str(exc), correlation_id=correlation_id, exc_info=True)
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


@app.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok", "service": settings.service_name}
