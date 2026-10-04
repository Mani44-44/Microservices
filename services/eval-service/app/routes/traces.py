"""
routes/traces.py
~~~~~~~~~~~~~~~~~
Called by the Agent Gateway only (fire-and-forget, one trace per end-to-end
agent run). No authentication required — internal use only, must not be
exposed publicly.
"""
import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models import AgentTrace
from app.schemas import TraceIn, TraceOut

log = structlog.get_logger()
settings = get_settings()

router = APIRouter(tags=["traces"])


@router.post("/traces")
async def create_trace(body: TraceIn, db: AsyncSession = Depends(get_db)):
    trace_id = body.trace_id or uuid.uuid4()

    cost = body.estimated_cost_usd
    if cost is None:
        # Simulated/illustrative only — no real LLM is used anywhere in this
        # project. See config.cost_per_mock_call_usd.
        cost = body.mock_llm_calls * settings.cost_per_mock_call_usd

    trace = AgentTrace(
        trace_id=trace_id,
        user_id=body.user_id,
        agent_type=body.agent_type,
        input_text=body.input_text,
        output_text=body.output_text,
        steps=[step.model_dump() for step in body.steps],
        guardrail_input_blocked=body.guardrail_input_blocked,
        guardrail_output_blocked=body.guardrail_output_blocked,
        total_duration_ms=body.total_duration_ms,
        mock_llm_calls=body.mock_llm_calls,
        estimated_cost_usd=cost,
        error=body.error,
    )
    db.add(trace)
    log.info("trace_ingested", trace_id=str(trace_id), agent_type=body.agent_type)
    return {"trace_id": str(trace_id)}


@router.get("/traces/{trace_id}", response_model=TraceOut)
async def get_trace(trace_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    trace = await db.get(AgentTrace, trace_id)
    if trace is None:
        raise HTTPException(status_code=404, detail=f"Trace {trace_id} not found")
    return trace


@router.get("/traces", response_model=list[TraceOut])
async def list_traces(
    agent_type: str | None = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(AgentTrace).order_by(AgentTrace.created_at.desc()).limit(limit)
    if agent_type:
        stmt = stmt.where(AgentTrace.agent_type == agent_type)
    result = await db.execute(stmt)
    return result.scalars().all()
