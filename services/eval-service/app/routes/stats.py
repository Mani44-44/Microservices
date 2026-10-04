from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import AgentTrace

router = APIRouter(tags=["stats"])


@router.get("/stats")
async def get_stats(db: AsyncSession = Depends(get_db)):
    total_result = await db.execute(select(func.count(AgentTrace.trace_id)))
    total_traces = total_result.scalar_one()

    if total_traces == 0:
        return {
            "total_traces": 0,
            "by_agent_type": {},
            "avg_duration_ms": 0.0,
            "total_estimated_cost_usd": 0.0,
            "guardrail_block_rate": 0.0,
            "error_rate": 0.0,
        }

    by_type_result = await db.execute(
        select(AgentTrace.agent_type, func.count(AgentTrace.trace_id)).group_by(
            AgentTrace.agent_type
        )
    )
    by_agent_type = {agent_type: count for agent_type, count in by_type_result.all()}

    avg_duration = (
        await db.execute(select(func.avg(AgentTrace.total_duration_ms)))
    ).scalar_one()
    total_cost = (
        await db.execute(select(func.sum(AgentTrace.estimated_cost_usd)))
    ).scalar_one()

    blocked_result = await db.execute(
        select(func.count(AgentTrace.trace_id)).where(
            (AgentTrace.guardrail_input_blocked.is_(True))
            | (AgentTrace.guardrail_output_blocked.is_(True))
        )
    )
    blocked_count = blocked_result.scalar_one()

    error_result = await db.execute(
        select(func.count(AgentTrace.trace_id)).where(AgentTrace.error.is_not(None))
    )
    error_count = error_result.scalar_one()

    return {
        "total_traces": total_traces,
        "by_agent_type": by_agent_type,
        "avg_duration_ms": float(avg_duration or 0.0),
        "total_estimated_cost_usd": float(total_cost or 0.0),
        "guardrail_block_rate": blocked_count / total_traces,
        "error_rate": error_count / total_traces,
    }
