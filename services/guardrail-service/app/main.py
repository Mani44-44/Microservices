import hashlib
import uuid
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends

from app.config import get_settings
from app.database import get_db, init_db
from app.models import GuardrailCheck
from app import checks, llm_judge
from libs.llm.groq_client import GroqClient, LLMError

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
    log.info("guardrail_service_startup", service=settings.service_name)
    await init_db()
    # LLM-as-judge second pass for borderline injection cases (see
    # check_input). Tests override app.state.llm_client with FakeGroqClient
    # before the lifespan-created client would matter; if no key is
    # configured here, borderline cases just fall back to the heuristic
    # verdict rather than failing startup.
    try:
        app.state.llm_client = GroqClient()
    except LLMError:
        log.warning("groq_client_unavailable", reason="GROQ_API_KEY not set")
        app.state.llm_client = None
    yield
    log.info("guardrail_service_shutdown", service=settings.service_name)


app = FastAPI(
    title="TicketFlow Guardrail Service",
    description="Deterministic, rule-based safety checks for AI agent input/output.",
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


# --- Schemas --------------------------------------------------------------
class CheckInputRequest(BaseModel):
    text: str


class CheckInputResponse(BaseModel):
    blocked: bool
    risk_score: float
    injection_detected: bool
    pii_found: list[str]
    redacted_text: str
    reasons: list[str]


class CheckOutputRequest(BaseModel):
    text: str
    context_chunks: list[str] = Field(default_factory=list)


class CheckOutputResponse(BaseModel):
    blocked: bool
    redacted_text: str
    pii_found: list[str]
    hallucination_flagged: bool
    reasons: list[str]


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def _log_check(db: AsyncSession, check_type: str, text: str, passed: bool, risk_score: float, details: dict):
    db.add(
        GuardrailCheck(
            check_type=check_type,
            text_hash=_hash(text),
            passed=passed,
            risk_score=risk_score,
            details=details,
        )
    )


# --- Routes -----------------------------------------------------------
# No authentication - internal use only. These endpoints must NOT be
# exposed publicly; they are called service-to-service by the agent
# gateway/support agent before/after LLM calls.
@app.post("/guardrails/check-input", response_model=CheckInputResponse)
async def check_input(body: CheckInputRequest, request: Request, db: AsyncSession = Depends(get_db)):
    injection = checks.detect_injection(body.text)
    pii_found = checks.detect_pii(body.text)
    redacted_text, _ = checks.redact(body.text)

    reasons = []
    if injection["is_injection"]:
        reasons.append(f"prompt_injection_patterns:{','.join(injection['matched_patterns'])}")
    if pii_found:
        reasons.append(f"pii_detected:{','.join(pii_found)}")

    # Block if injection risk crosses the threshold, or PII is present
    # (raw PII must never be forwarded to an LLM prompt - see CLAUDE-level
    # security convention "never pass PII to LLM without redaction").
    blocked = injection["risk_score"] >= settings.injection_block_threshold or bool(pii_found)

    # Borderline second pass: the heuristic scored *some* signal but not
    # enough to auto-block, and there's no PII already forcing a block.
    # Clear-cut allows (score 0) and clear-cut blocks (score >= threshold,
    # or PII) never reach the LLM - only genuinely uncertain cases do.
    borderline = 0 < injection["risk_score"] < settings.injection_block_threshold and not pii_found
    llm_client = getattr(request.app.state, "llm_client", None)
    if borderline and llm_client is not None:
        try:
            verdict = await llm_judge.judge_injection(llm_client, body.text, injection["matched_patterns"])
            blocked = verdict["blocked"]
            reasons.append(f"llm_judge:{verdict['category']}:{verdict['reason']}")
        except LLMError as e:
            log.warning("llm_judge_fallback", error=str(e), heuristic_verdict=blocked)

    await _log_check(
        db, "INPUT", body.text, passed=not blocked, risk_score=injection["risk_score"],
        details={"injection": injection, "pii_found": pii_found},
    )

    return CheckInputResponse(
        blocked=blocked,
        risk_score=injection["risk_score"],
        injection_detected=injection["is_injection"],
        pii_found=pii_found,
        redacted_text=redacted_text,
        reasons=reasons,
    )


@app.post("/guardrails/check-output", response_model=CheckOutputResponse)
async def check_output(body: CheckOutputRequest, db: AsyncSession = Depends(get_db)):
    redacted_text, pii_found = checks.redact(body.text)
    hallucination = checks.check_hallucination(body.text, body.context_chunks)

    reasons = []
    if pii_found:
        reasons.append(f"pii_detected:{','.join(pii_found)}")
    if hallucination["hallucination_flagged"]:
        reasons.append(f"unsupported_claims:{','.join(hallucination['unsupported_claims'])}")

    # Output is blocked only on PII leakage (a hard rule). A flagged
    # hallucination is surfaced but not auto-blocked - it needs a human/
    # caller-side review since the heuristic is coarse (see checks.py
    # docstring), and blocking every flagged number would make the
    # support agent unusable for anything quantitative.
    blocked = bool(pii_found)

    await _log_check(
        db, "OUTPUT", body.text, passed=not blocked, risk_score=1.0 if pii_found else 0.0,
        details={"pii_found": pii_found, "hallucination": hallucination},
    )

    return CheckOutputResponse(
        blocked=blocked,
        redacted_text=redacted_text,
        pii_found=pii_found,
        hallucination_flagged=hallucination["hallucination_flagged"],
        reasons=reasons,
    )


@app.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok"}
