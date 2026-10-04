"""
tools.py
~~~~~~~~
The support agent's tool functions. Each is a plain async function so it
can be unit tested directly, independent of the /handle routing layer.
"""
import json
from datetime import datetime, timezone

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import clients
from app.models import RefundRequest, SupportTicket
from app.rules_engine import RefundRulesEngine
from libs.llm.groq_client import LLMError

log = structlog.get_logger()
rules_engine = RefundRulesEngine()

_REFUND_MESSAGE_SYSTEM_PROMPT = (
    "You are a professional, empathetic customer support agent for TicketFlow, "
    "a ticket-booking platform. You will be given a JSON refund decision that has "
    "ALREADY been made by a deterministic rules engine — approved/denied, the exact "
    "dollar amount, and the policy reason. Write a short (2-4 sentence) customer-facing "
    "message explaining this decision. Do not invent, recompute, or alter the amount or "
    "the decision — state them exactly as given. If a discrepancy_note is present, "
    "incorporate it naturally. Do not add disclaimers about being an AI."
)


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class DownstreamError(Exception):
    """Raised when a call to another service fails (network error or non-2xx)."""


async def get_booking_details(booking_id: str, user_bearer_token: str) -> dict:
    try:
        return await clients.get_booking(booking_id, user_bearer_token)
    except (httpx.HTTPError, httpx.HTTPStatusError) as exc:
        log.warning("get_booking_details_failed", booking_id=booking_id, error=str(exc))
        raise DownstreamError(f"Could not look up booking {booking_id}") from exc


async def check_refund_eligibility(booking_id: str, user_bearer_token: str) -> dict:
    """Fetch booking + event, apply RefundRulesEngine. Returns percentage/amount/reason."""
    try:
        booking = await clients.get_booking(booking_id, user_bearer_token)
        event = await clients.get_event(booking["event_id"])
    except (httpx.HTTPError, httpx.HTTPStatusError) as exc:
        log.warning("check_refund_eligibility_failed", booking_id=booking_id, error=str(exc))
        raise DownstreamError(f"Could not check refund eligibility for booking {booking_id}") from exc

    event_date = _parse_dt(event["event_date"])
    now = datetime.now(timezone.utc)
    result = rules_engine.check_eligibility(event_date, event["status"], now)

    total_amount = float(booking["total_amount"])
    eligible_amount = round(total_amount * result["refund_percentage"] / 100, 2)

    return {
        "refund_percentage": result["refund_percentage"],
        "reason": result["reason"],
        "rule": result["rule"],
        "total_amount": total_amount,
        "eligible_amount": eligible_amount,
        "currency": booking.get("currency", "USD"),
    }


async def cancel_booking(booking_id: str, reason: str, user_bearer_token: str) -> dict:
    try:
        return await clients.release_booking(booking_id, user_bearer_token)
    except (httpx.HTTPError, httpx.HTTPStatusError) as exc:
        log.warning("cancel_booking_failed", booking_id=booking_id, error=str(exc))
        raise DownstreamError(f"Could not cancel booking {booking_id}") from exc


async def request_refund(
    db: AsyncSession,
    booking_id: str,
    amount: float,
    user_id: str,
    user_bearer_token: str,
    reason: str = "",
) -> dict:
    """
    Always trusts the rules-engine-computed eligible amount, never the
    caller-supplied `amount` at face value. If they differ, the response
    explains the discrepancy rather than silently overriding it.
    """
    try:
        eligibility = await check_refund_eligibility(booking_id, user_bearer_token)
        payment = await clients.get_payment_by_booking(booking_id)
    except DownstreamError:
        raise
    except (httpx.HTTPError, httpx.HTTPStatusError) as exc:
        log.warning("request_refund_lookup_failed", booking_id=booking_id, error=str(exc))
        raise DownstreamError(f"Could not resolve payment for booking {booking_id}") from exc

    eligible_amount = eligibility["eligible_amount"]
    discrepancy_note = None
    if round(amount, 2) != round(eligible_amount, 2):
        discrepancy_note = (
            f"You requested {amount}, but the policy-eligible amount is "
            f"{eligible_amount} ({eligibility['reason']}). Processing the eligible amount."
        )

    if eligible_amount <= 0:
        refund_request = RefundRequest(
            booking_id=booking_id,
            user_id=user_id,
            amount=0,
            reason=reason,
            status="DENIED",
        )
        db.add(refund_request)
        await db.flush()
        return {
            "success": False,
            "status": "DENIED",
            "eligible_amount": 0,
            "reason": eligibility["reason"],
            "discrepancy_note": discrepancy_note,
            "refund_request_id": str(refund_request.id),
        }

    try:
        refund_result = await clients.refund_payment(
            payment["payment_id"], eligible_amount, reason or eligibility["reason"]
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 409:
            refund_request = RefundRequest(
                booking_id=booking_id,
                user_id=user_id,
                amount=eligible_amount,
                reason=reason,
                status="DENIED",
            )
            db.add(refund_request)
            await db.flush()
            return {
                "success": False,
                "status": "DENIED",
                "reason": "Payment is not in a refundable state (not SUCCEEDED).",
                "discrepancy_note": discrepancy_note,
                "refund_request_id": str(refund_request.id),
            }
        log.warning("request_refund_call_failed", booking_id=booking_id, error=str(exc))
        raise DownstreamError(f"Refund call failed for booking {booking_id}") from exc
    except httpx.HTTPError as exc:
        log.warning("request_refund_call_failed", booking_id=booking_id, error=str(exc))
        raise DownstreamError(f"Refund call failed for booking {booking_id}") from exc

    refund_request = RefundRequest(
        booking_id=booking_id,
        user_id=user_id,
        amount=eligible_amount,
        reason=reason,
        status="PROCESSED",
        processed_at=datetime.now(timezone.utc),
    )
    db.add(refund_request)
    await db.flush()

    return {
        "success": True,
        "status": "PROCESSED",
        "eligible_amount": eligible_amount,
        "reason": eligibility["reason"],
        "discrepancy_note": discrepancy_note,
        "refund_request_id": str(refund_request.id),
        "provider_refund": refund_result,
    }


async def search_policy(query: str) -> dict:
    try:
        result = await clients.search_policy_docs(query, category="support")
    except (httpx.HTTPError, httpx.HTTPStatusError) as exc:
        log.warning("search_policy_failed", query=query, error=str(exc))
        raise DownstreamError("Could not search policy documents") from exc

    results = result.get("results", [])
    if not results:
        return {"found": False, "answer": None}
    return {"found": True, "answer": results[0]["content"]}


async def create_support_ticket(
    db: AsyncSession,
    user_id: str,
    conversation_id: str,
    booking_id: str | None,
    issue_type: str,
    priority: str = "MEDIUM",
) -> SupportTicket:
    ticket = SupportTicket(
        user_id=user_id,
        conversation_id=conversation_id,
        booking_id=booking_id,
        issue_type=issue_type,
        priority=priority,
        status="OPEN",
    )
    db.add(ticket)
    await db.flush()
    return ticket


def _template_refund_message(eligibility: dict, result: dict) -> str:
    """Original deterministic template — used as the fallback text when the
    LLM call fails, and as the LLM's only source of truth for the numbers."""
    parts = []
    if result["success"]:
        parts.append(
            f"Your refund of {result['eligible_amount']} has been processed "
            f"({eligibility['reason']})."
        )
    else:
        parts.append(f"Your refund could not be processed: {result['reason']}.")
    if result.get("discrepancy_note"):
        parts.append(result["discrepancy_note"])
    return " ".join(parts)


async def generate_refund_message(llm_client, eligibility: dict, result: dict) -> str:
    """
    Builds the customer-facing refund message. The refund AMOUNT and
    approved/denied decision come only from `eligibility`/`result` (the
    RefundRulesEngine's output, already computed) — the LLM is given those
    numbers as fixed facts and only asked to phrase them, never to compute
    or alter them. Falls back to the deterministic template on any LLM
    error/timeout so a Groq hiccup never blocks a refund response.
    """
    template = _template_refund_message(eligibility, result)
    if llm_client is None:
        return template

    decision = {
        "approved": result["success"],
        "amount": result.get("eligible_amount", 0),
        "reason": result.get("reason") or eligibility["reason"],
        "discrepancy_note": result.get("discrepancy_note"),
    }
    try:
        text = await llm_client.complete(
            system=_REFUND_MESSAGE_SYSTEM_PROMPT,
            user=json.dumps(decision),
            max_tokens=250,
        )
        return text.strip()
    except LLMError as exc:
        log.warning("refund_message_llm_failed_fallback_to_template", error=str(exc))
        return template


async def escalate_to_human(
    db: AsyncSession,
    conversation_id: str,
    reason: str,
    user_id: str = "",
    booking_id: str | None = None,
) -> SupportTicket:
    result = await db.execute(
        select(SupportTicket)
        .where(SupportTicket.conversation_id == conversation_id)
        .order_by(SupportTicket.created_at.desc())
    )
    ticket = result.scalars().first()

    if ticket is None:
        ticket = SupportTicket(
            user_id=user_id,
            conversation_id=conversation_id,
            booking_id=booking_id,
            issue_type="ESCALATION",
            priority="HIGH",
            status="ESCALATED",
        )
        db.add(ticket)
    else:
        ticket.status = "ESCALATED"
        ticket.priority = "HIGH"

    await db.flush()
    log.info("escalated_to_human", conversation_id=conversation_id, reason=reason, ticket_id=str(ticket.id))
    return ticket
