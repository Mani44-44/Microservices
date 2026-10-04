import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db, init_db
from app.models import Document
from app.rag import ingest_document, search_chunks
from app.schemas import DocumentIn, DocumentOut, SearchIn, SearchOut
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
    log.info("rag_service_startup", service=settings.service_name)
    app.state.llm_client = GroqClient()
    await init_db()

    from app.database import async_session_factory
    from app.seed_data import SEED_DOCUMENTS

    async with async_session_factory() as db:
        count = (await db.execute(select(func.count()).select_from(Document))).scalar_one()
        if count == 0:
            for doc in SEED_DOCUMENTS:
                await ingest_document(db, **doc)
            await db.commit()
            log.info("seed_data_loaded", documents=len(SEED_DOCUMENTS))

    log.info("database_initialised")
    yield
    log.info("rag_service_shutdown", service=settings.service_name)


app = FastAPI(
    title="TicketFlow RAG Retrieval Service",
    description="Document ingestion, chunking, mock-embedding, and vector search "
    "over travel/support knowledge.",
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
# Routes
#
# No authentication on these endpoints — this service is called only by
# other TicketFlow services over the internal network (agent-gateway,
# travel-planner-agent, support-agent), never exposed to end users directly.
# ---------------------------------------------------------------------------


@app.post("/documents", response_model=DocumentOut)
async def create_document(body: DocumentIn, db: AsyncSession = Depends(get_db)):
    document_id, chunks_created = await ingest_document(
        db, content=body.content, category=body.category, source=body.source, metadata=body.metadata
    )
    return DocumentOut(document_id=document_id, chunks_created=chunks_created)


@app.post("/search", response_model=SearchOut)
async def search(body: SearchIn, request: Request, db: AsyncSession = Depends(get_db)):
    results = await search_chunks(
        db,
        query=body.query,
        category=body.category,
        top_k=body.top_k,
        llm_client=request.app.state.llm_client,
    )
    return SearchOut(results=results)


@app.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
async def root():
    return {"service": settings.service_name, "status": "running"}
