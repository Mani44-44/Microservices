"""
orchestrator.py
~~~~~~~~~~~~~~~
The chat safety-orchestration flow: input guardrail -> intent classify ->
route to sub-agent/RAG/canned reply -> output guardrail -> trace -> persist.
"""
import time
import uuid
from datetime import datetime, timezone

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app import clients
from app.intent import classify_llm
from app.models import AgentDecision, Conversation, Message

log = structlog.get_logger()

_CHITCHAT_REPLIES = {
    "default": "Hello! How can I help with your trip or booking today?",
    "thanks": "You're welcome! Anything else I can help with?",
}

_ESCALATION_REPLY = "I'm connecting you to a human agent who can help further."
_BLOCKED_REPLY = "I cannot process that request."

_INTENT_TO_CONVERSATION_TYPE = {
    "TRAVEL_PLANNING": "TRAVEL_PLANNING",
    "BOOKING_INQUIRY": "SUPPORT",
    "REFUND_REQUEST": "SUPPORT",
    "GENERAL_QA": "GENERAL",
    "CHITCHAT": "GENERAL",
    "ESCALATION": "GENERAL",
}

_INTENT_TO_AGENT_TYPE = {
    "TRAVEL_PLANNING": "travel_planner",
    "BOOKING_INQUIRY": "support",
    "REFUND_REQUEST": "support",
    "GENERAL_QA": "general",
    "CHITCHAT": "general",
    "ESCALATION": "general",
}


async def _get_or_create_conversation(db: AsyncSession, user_id: uuid.UUID, conversation_id) -> Conversation:
    if conversation_id is not None:
        conv = await db.get(Conversation, conversation_id)
        if conv is not None and conv.user_id == user_id:
            return conv
    conv = Conversation(user_id=user_id, conversation_type="GENERAL", status="ACTIVE")
    db.add(conv)
    await db.flush()
    return conv


async def _save_message(db: AsyncSession, conversation_id, sender: str, content: str, message_type: str = "TEXT", latency_ms: int | None = None) -> Message:
    msg = Message(
        conversation_id=conversation_id,
        sender=sender,
        content=content,
        message_type=message_type,
        latency_ms=latency_ms,
    )
    db.add(msg)
    await db.flush()
    return msg


async def handle_chat(db: AsyncSession, user_id: uuid.UUID, bearer_token: str, message: str, conversation_id, llm_client) -> dict:
    start = time.monotonic()
    conv = await _get_or_create_conversation(db, user_id, conversation_id)

    await _save_message(db, conv.id, sender="USER", content=message)

    # --- Step 2: input guardrail ---
    guardrail_in = await clients.check_guardrail_input(message)
    if guardrail_in.get("blocked"):
        reasons = guardrail_in.get("reasons", [])
        await _save_message(
            db, conv.id, sender="SYSTEM",
            content=f"Message blocked by input guardrail: {reasons}",
        )
        total_ms = int((time.monotonic() - start) * 1000)
        await clients.send_trace({
            "trace_id": str(uuid.uuid4()),
            "user_id": str(user_id),
            "agent_type": "general",
            "input_text": message,
            "output_text": _BLOCKED_REPLY,
            "steps": [],
            "guardrail_input_blocked": True,
            "guardrail_output_blocked": False,
            "total_duration_ms": total_ms,
            "mock_llm_calls": 0,
            "error": None,
        })
        return {
            "conversation_id": conv.id,
            "response": _BLOCKED_REPLY,
            "intent": "BLOCKED",
            "blocked": True,
            "reasons": reasons,
        }

    # --- Step 3: intent classification ---
    classification = await classify_llm(llm_client, message)
    intent = classification["intent"]

    # ponytail: classification is per-message with no conversation memory.
    # Once travel_planner asks a constraint-gathering follow-up ("what
    # dates?"), a free-text reply ("I love museums") matches no
    # TRAVEL_PLANNING keyword and falls through to GENERAL_QA, derailing
    # the multi-turn planning flow. Sticky-route weak (GENERAL_QA/CHITCHAT)
    # classifications back to an already-active travel planning
    # conversation; explicit REFUND/BOOKING/ESCALATION asks still win since
    # classify() already returns those directly, bypassing this branch.
    # Upgrade path: real "awaiting constraint" conversation-state tracking
    # if this heuristic proves too broad.
    if (
        intent in ("GENERAL_QA", "CHITCHAT")
        and conv.conversation_type == "TRAVEL_PLANNING"
        and conv.status == "ACTIVE"
    ):
        intent = "TRAVEL_PLANNING"
        classification = {
            "intent": intent,
            "confidence": 0.6,
            "reasoning": "Sticky-routed: continuing an active travel planning conversation.",
        }

    routed_to = _INTENT_TO_AGENT_TYPE[intent]
    context_chunks: list[str] = []

    db.add(AgentDecision(
        conversation_id=conv.id,
        intent=intent,
        confidence_score=classification["confidence"],
        routed_to=routed_to,
        reasoning=classification["reasoning"],
    ))

    conv.conversation_type = _INTENT_TO_CONVERSATION_TYPE[intent]

    # --- Step 4: routing ---
    if intent == "TRAVEL_PLANNING":
        result = await clients.call_travel_planner(str(conv.id), str(user_id), message)
        response_text = result.get("response_text", "The travel planner is temporarily unavailable.")
    elif intent in ("BOOKING_INQUIRY", "REFUND_REQUEST"):
        result = await clients.call_support_agent(str(conv.id), str(user_id), message, intent, bearer_token)
        response_text = result.get("response_text", "The support agent is temporarily unavailable.")
    elif intent == "GENERAL_QA":
        results = await clients.rag_search(message, top_k=3)
        if results:
            content = results[0].get("content", "")
            response_text = f"Based on our info: {content}"
            context_chunks = [r.get("content", "") for r in results]
        else:
            response_text = "I don't have information on that."
    elif intent == "CHITCHAT":
        text_lower = message.lower()
        if "thank" in text_lower:
            response_text = _CHITCHAT_REPLIES["thanks"]
        else:
            response_text = _CHITCHAT_REPLIES["default"]
    else:  # ESCALATION
        conv.status = "ESCALATED"
        response_text = _ESCALATION_REPLY

    # --- Step 5: output guardrail ---
    guardrail_out = await clients.check_guardrail_output(response_text, context_chunks)
    final_text = guardrail_out.get("redacted_text", response_text)

    total_ms = int((time.monotonic() - start) * 1000)

    # --- Step 6: fire-and-forget trace ---
    await clients.send_trace({
        "trace_id": str(uuid.uuid4()),
        "user_id": str(user_id),
        "agent_type": routed_to,
        "input_text": message,
        "output_text": final_text,
        "steps": [],
        "guardrail_input_blocked": False,
        "guardrail_output_blocked": bool(guardrail_out.get("blocked")),
        "total_duration_ms": total_ms,
        "mock_llm_calls": 0,
        "error": None,
    })

    # --- Step 7: persist agent response ---
    await _save_message(db, conv.id, sender="AGENT", content=final_text, latency_ms=total_ms)
    conv.last_message_at = datetime.now(timezone.utc)

    return {
        "conversation_id": conv.id,
        "response": final_text,
        "intent": intent,
        "blocked": False,
        "reasons": [],
    }
