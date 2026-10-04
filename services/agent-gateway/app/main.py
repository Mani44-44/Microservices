import uuid
from contextlib import asynccontextmanager

import redis.asyncio as aioredis
import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.database import init_db
from app.routes import agent
from libs.llm.groq_client import GroqClient

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("agent_gateway_startup", service=settings.service_name)
    await init_db()
    app.state.redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    app.state.llm_client = GroqClient()
    yield
    await app.state.redis.aclose()
    log.info("agent_gateway_shutdown", service=settings.service_name)


app = FastAPI(
    title="TicketFlow Agent Gateway",
    description="Root agent: conversation routing, intent classification, and safety orchestration for TicketFlow's AI agents.",
    version="1.0.0",
    lifespan=lifespan,
)

from libs.observability import add_security_headers, instrument_metrics, instrument_tracing

instrument_metrics(app, settings.service_name)
add_security_headers(app)
instrument_tracing(app, settings.service_name)

# Browser UI (services/ui) calls this on a different port than the main
# gateway - needs its own CORS, same dev-only allow-all as services/gateway.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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

app.include_router(agent.router)


# ---------------------------------------------------------------------------
# Health / root
# ---------------------------------------------------------------------------


@app.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok", "service": settings.service_name}


@app.get("/", include_in_schema=False)
async def root():
    return {"service": settings.service_name, "status": "running"}
