from contextlib import asynccontextmanager

import structlog
import time
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.database import init_db
from app.routes.users import router

log = structlog.get_logger()
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: initialise DB on startup, log shutdown."""
    await init_db()
    log.info("user_service_started", port=8001)
    yield
    log.info("user_service_stopped")


app = FastAPI(
    title="TicketFlow User Service",
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
    """Attach a correlation ID to every request/response cycle."""
    correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
    request.state.correlation_id = correlation_id
    start = time.time()
    response = await call_next(request)
    response.headers["X-Correlation-ID"] = correlation_id
    duration_ms = (time.time() - start) * 1000
    log.info(
        "request_completed",
        method=request.method,
        path=request.url.path,
        status=response.status_code,
        duration_ms=round(duration_ms, 2),
        correlation_id=correlation_id,
    )
    return response

# ---------------------------------------------------------------------------
# HTTPException handler — unwrap {"error": {...}} details into a top-level
# envelope instead of FastAPI's default {"detail": ...} wrapping.
# ---------------------------------------------------------------------------


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    correlation_id = getattr(request.state, "correlation_id", None) or request.headers.get(
        "X-Correlation-ID", str(uuid.uuid4())
    )
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        content = exc.detail
        if not content["error"].get("correlation_id"):
            content["error"]["correlation_id"] = correlation_id
    else:
        content = {
            "error": {
                "code": "HTTP_ERROR",
                "message": str(exc.detail),
                "details": {},
                "correlation_id": correlation_id,
            }
        }
    return JSONResponse(status_code=exc.status_code, content=content, headers=exc.headers)



app.include_router(router)
