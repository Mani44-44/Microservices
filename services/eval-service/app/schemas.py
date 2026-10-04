import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class TraceStep(BaseModel):
    name: str
    duration_ms: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class TraceIn(BaseModel):
    trace_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    agent_type: str
    input_text: str
    output_text: str
    steps: list[TraceStep] = Field(default_factory=list)
    guardrail_input_blocked: bool = False
    guardrail_output_blocked: bool = False
    total_duration_ms: int = 0
    mock_llm_calls: int = 0
    estimated_cost_usd: float | None = None
    error: str | None = None


class TraceOut(BaseModel):
    trace_id: uuid.UUID
    user_id: uuid.UUID | None
    agent_type: str
    input_text: str
    output_text: str
    steps: list[dict[str, Any]]
    guardrail_input_blocked: bool
    guardrail_output_blocked: bool
    total_duration_ms: int
    mock_llm_calls: int
    estimated_cost_usd: float
    error: str | None
    created_at: datetime

    class Config:
        from_attributes = True
