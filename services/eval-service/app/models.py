import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class AgentTrace(Base):
    """One row per end-to-end agent run, as reported by the Agent Gateway.

    The gateway collects the full step/tool-call breakdown for a run and
    posts it here in a single call (fire-and-forget) — this service does not
    accept incremental per-step writes from multiple callers.
    """

    __tablename__ = "agent_traces"

    trace_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    agent_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    input_text: Mapped[str] = mapped_column(Text, nullable=False)
    output_text: Mapped[str] = mapped_column(Text, nullable=False)
    steps: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    guardrail_input_blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    guardrail_output_blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    total_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    mock_llm_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated_cost_usd: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )

    def __repr__(self) -> str:
        return f"<AgentTrace trace_id={self.trace_id} agent_type={self.agent_type}>"
