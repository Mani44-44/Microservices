"""
routes/handle.py
~~~~~~~~~~~~~~~~~
Internal use only — called by the Agent Gateway, not exposed to end users
directly. No auth on this endpoint itself; the end-user's own bearer token
travels in the request body (`user_bearer_token`) and is forwarded as a real
`Authorization` header on outgoing calls to booking-service.
"""
import structlog
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app import tools
from app.database import get_db
from app.routing import extract_amount, extract_booking_id
from app.schemas import HandleRequest, HandleResponse

log = structlog.get_logger()
router = APIRouter(tags=["handle"])


@router.post("/handle", response_model=HandleResponse, summary="Handle one support-agent turn (internal use only)")
async def handle(body: HandleRequest, request: Request, db: AsyncSession = Depends(get_db)):
    log.info(
        "handle_request",
        conversation_id=body.conversation_id,
        intent=body.intent,
        correlation_id=getattr(request.state, "correlation_id", None),
    )

    booking_id = extract_booking_id(body.message)

    try:
        if body.intent == "REFUND_REQUEST":
            return await _handle_refund_request(db, body, booking_id, request.app.state.llm_client)
        return await _handle_booking_inquiry(db, body, booking_id)
    except tools.DownstreamError as exc:
        ticket = await tools.escalate_to_human(
            db, body.conversation_id, str(exc), user_id=body.user_id, booking_id=booking_id
        )
        return HandleResponse(
            response_text=(
                f"Sorry, I couldn't complete that: {exc}. "
                "I've escalated this to a human agent who will follow up."
            ),
            ticket_id=str(ticket.id),
            escalated=True,
        )


async def _handle_refund_request(
    db: AsyncSession, body: HandleRequest, booking_id: str | None, llm_client=None
) -> HandleResponse:
    if booking_id is None:
        return HandleResponse(
            response_text="I can help with that refund — could you share your booking ID?",
            escalated=False,
        )

    eligibility = await tools.check_refund_eligibility(booking_id, body.user_bearer_token)
    requested_amount = extract_amount(body.message)
    amount_to_use = requested_amount if requested_amount is not None else eligibility["eligible_amount"]

    result = await tools.request_refund(
        db,
        booking_id=booking_id,
        amount=amount_to_use,
        user_id=body.user_id,
        user_bearer_token=body.user_bearer_token,
    )

    ticket = await tools.create_support_ticket(
        db,
        user_id=body.user_id,
        conversation_id=body.conversation_id,
        booking_id=booking_id,
        issue_type="REFUND_REQUEST",
        priority="MEDIUM",
    )

    response_text = await tools.generate_refund_message(llm_client, eligibility, result)

    return HandleResponse(response_text=response_text, ticket_id=str(ticket.id), escalated=False)


async def _handle_booking_inquiry(db: AsyncSession, body: HandleRequest, booking_id: str | None) -> HandleResponse:
    if booking_id:
        booking = await tools.get_booking_details(booking_id, body.user_bearer_token)
        response_text = (
            f"Booking {booking['id']}: status={booking['status']}, "
            f"total={booking['total_amount']} {booking.get('currency', '')}, "
            f"event_id={booking['event_id']}."
        )
        return HandleResponse(response_text=response_text, escalated=False)

    # No booking_id in the message -> treat as a general policy question.
    result = await tools.search_policy(body.message)
    if not result["found"]:
        ticket = await tools.escalate_to_human(
            db, body.conversation_id, "No policy match found", user_id=body.user_id
        )
        return HandleResponse(
            response_text="I don't have that information. Let me escalate to a human agent.",
            ticket_id=str(ticket.id),
            escalated=True,
        )
    return HandleResponse(response_text=result["answer"], escalated=False)
