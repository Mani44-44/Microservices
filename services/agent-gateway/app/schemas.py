from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[UUID] = None


class ChatResponse(BaseModel):
    conversation_id: UUID
    response: str
    intent: str
    blocked: bool
    reasons: list[str] = []


class MessageOut(BaseModel):
    id: UUID
    sender: str
    content: str
    message_type: str
    created_at: datetime
    latency_ms: Optional[int] = None

    model_config = {"from_attributes": True}


class ConversationOut(BaseModel):
    id: UUID
    user_id: UUID
    conversation_type: str
    status: str
    created_at: datetime
    last_message_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class ConversationDetailOut(ConversationOut):
    messages: list[MessageOut] = []


class EscalateRequest(BaseModel):
    reason: str


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict = {}
    correlation_id: Optional[str] = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
