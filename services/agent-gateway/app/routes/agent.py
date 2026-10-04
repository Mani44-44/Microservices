"""
routes/agent.py
~~~~~~~~~~~~~~~~
User-facing entry point for all AI interactions.
"""
import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_bearer_token, get_current_user_id
from app.database import get_db
from app.models import Conversation
from app.orchestrator import handle_chat
from app.rate_limit import check_and_increment
from app.schemas import (
    ChatRequest,
    ChatResponse,
    ConversationDetailOut,
    ConversationOut,
    EscalateRequest,
)

log = structlog.get_logger()

router = APIRouter(prefix="/api/v1/agent", tags=["agent"])


def _rate_limited_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={
            "error": {
                "code": "RATE_LIMITED",
                "message": "You have exceeded 20 messages per hour. Please try again later.",
                "details": {},
            }
        },
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    request: Request,
    user_id: uuid.UUID = Depends(get_current_user_id),
    bearer_token: str = Depends(get_bearer_token),
    db: AsyncSession = Depends(get_db),
):
    allowed = await check_and_increment(request.app.state.redis, str(user_id))
    if not allowed:
        log.warning("rate_limited", user_id=str(user_id))
        raise _rate_limited_error()

    result = await handle_chat(
        db, user_id, bearer_token, body.message, body.conversation_id, request.app.state.llm_client
    )
    return ChatResponse(**result)


@router.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Conversation).where(Conversation.user_id == user_id).order_by(Conversation.created_at.desc())
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/conversations/{conversation_id}", response_model=ConversationDetailOut)
async def get_conversation(
    conversation_id: uuid.UUID,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    conv = await db.get(Conversation, conversation_id)
    if conv is None or conv.user_id != user_id:
        raise HTTPException(status_code=404, detail=f"Conversation {conversation_id} not found")
    return conv


@router.delete("/conversations/{conversation_id}", response_model=ConversationOut)
async def end_conversation(
    conversation_id: uuid.UUID,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    conv = await db.get(Conversation, conversation_id)
    if conv is None or conv.user_id != user_id:
        raise HTTPException(status_code=404, detail=f"Conversation {conversation_id} not found")
    conv.status = "ENDED"
    await db.flush()
    return conv


@router.post("/conversations/{conversation_id}/escalate", response_model=ConversationOut)
async def escalate_conversation(
    conversation_id: uuid.UUID,
    body: EscalateRequest,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    conv = await db.get(Conversation, conversation_id)
    if conv is None or conv.user_id != user_id:
        raise HTTPException(status_code=404, detail=f"Conversation {conversation_id} not found")
    conv.status = "ESCALATED"
    await db.flush()
    log.info("conversation_escalated", conversation_id=str(conversation_id), reason=body.reason)
    return conv


@router.get("/health", include_in_schema=False)
async def health():
    return {"status": "ok"}
