from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from libs.llm.groq_client import LLMError

pytestmark = pytest.mark.asyncio

FUTURE_EVENT_DATE = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
REFUND_BOOKING = {
    "id": "b1",
    "user_id": "u1",
    "event_id": "e1",
    "status": "CONFIRMED",
    "total_amount": "100.00",
    "currency": "USD",
}
REFUND_MESSAGE = (
    "conv-refund",
    "u1",
    "Please refund booking 3fa85f64-5717-4562-b3fc-2c963f66afa6",
)


@patch("app.tools.clients.get_booking", new_callable=AsyncMock)
async def test_handle_booking_inquiry_with_booking_id(mock_get_booking, client):
    mock_get_booking.return_value = {
        "id": "b1",
        "event_id": "e1",
        "status": "CONFIRMED",
        "total_amount": "100.00",
        "currency": "USD",
    }
    resp = await client.post(
        "/handle",
        json={
            "conversation_id": "conv-1",
            "user_id": "u1",
            "message": "What's the status of booking 3fa85f64-5717-4562-b3fc-2c963f66afa6?",
            "intent": "BOOKING_INQUIRY",
            "user_bearer_token": "tok",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "CONFIRMED" in body["response_text"]
    assert body["escalated"] is False


@patch("app.tools.clients.get_booking", new_callable=AsyncMock)
async def test_handle_downstream_failure_escalates_gracefully(mock_get_booking, client):
    mock_get_booking.side_effect = httpx.ConnectError("booking-service unreachable")

    resp = await client.post(
        "/handle",
        json={
            "conversation_id": "conv-2",
            "user_id": "u1",
            "message": "What's the status of booking 3fa85f64-5717-4562-b3fc-2c963f66afa6?",
            "intent": "BOOKING_INQUIRY",
            "user_bearer_token": "tok",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["escalated"] is True
    assert body["ticket_id"] is not None


@patch("app.tools.clients.search_policy_docs", new_callable=AsyncMock)
async def test_handle_booking_inquiry_no_id_searches_policy(mock_search, client):
    mock_search.return_value = {"results": []}
    resp = await client.post(
        "/handle",
        json={
            "conversation_id": "conv-3",
            "user_id": "u1",
            "message": "What is your refund policy in general?",
            "intent": "BOOKING_INQUIRY",
            "user_bearer_token": "tok",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["escalated"] is True
    assert "escalate" in body["response_text"].lower()


async def test_handle_refund_request_no_booking_id_asks_for_it(client):
    resp = await client.post(
        "/handle",
        json={
            "conversation_id": "conv-4",
            "user_id": "u1",
            "message": "I want a refund",
            "intent": "REFUND_REQUEST",
            "user_bearer_token": "tok",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["escalated"] is False
    assert "booking" in body["response_text"].lower()


@patch("app.tools.clients.refund_payment", new_callable=AsyncMock)
@patch("app.tools.clients.get_payment_by_booking", new_callable=AsyncMock)
@patch("app.tools.clients.get_event", new_callable=AsyncMock)
@patch("app.tools.clients.get_booking", new_callable=AsyncMock)
async def test_handle_refund_request_uses_llm_generated_message(
    mock_get_booking, mock_get_event, mock_get_payment, mock_refund, client, fake_llm
):
    """The refund AMOUNT must still come from the deterministic rules engine
    (100.0, full-refund-window), regardless of what text the LLM produces."""
    mock_get_booking.return_value = REFUND_BOOKING
    mock_get_event.return_value = {"event_date": FUTURE_EVENT_DATE, "status": "PUBLISHED"}
    mock_get_payment.return_value = {
        "payment_id": "p1", "booking_id": "b1", "amount": 100.0, "currency": "USD", "status": "SUCCEEDED",
    }
    mock_refund.return_value = {"id": "r1", "payment_id": "p1", "amount": "100.00", "status": "SUCCEEDED"}
    fake_llm.complete_responses.append(
        "Great news! We've processed a full refund of 100.0 for your booking."
    )

    resp = await client.post(
        "/handle",
        json={
            "conversation_id": REFUND_MESSAGE[0],
            "user_id": REFUND_MESSAGE[1],
            "message": REFUND_MESSAGE[2],
            "intent": "REFUND_REQUEST",
            "user_bearer_token": "tok",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["response_text"] == "Great news! We've processed a full refund of 100.0 for your booking."
    # The amount the LLM was told about is the rules-engine amount, not a
    # value the LLM invented.
    assert fake_llm.calls[0]["user"].count("100.0") >= 1
    mock_refund.assert_awaited_once_with("p1", 100.0, "Full refund window")


@patch("app.tools.clients.refund_payment", new_callable=AsyncMock)
@patch("app.tools.clients.get_payment_by_booking", new_callable=AsyncMock)
@patch("app.tools.clients.get_event", new_callable=AsyncMock)
@patch("app.tools.clients.get_booking", new_callable=AsyncMock)
async def test_handle_refund_request_falls_back_to_template_on_llm_error(
    mock_get_booking, mock_get_event, mock_get_payment, mock_refund, client, fake_llm
):
    mock_get_booking.return_value = REFUND_BOOKING
    mock_get_event.return_value = {"event_date": FUTURE_EVENT_DATE, "status": "PUBLISHED"}
    mock_get_payment.return_value = {
        "payment_id": "p1", "booking_id": "b1", "amount": 100.0, "currency": "USD", "status": "SUCCEEDED",
    }
    mock_refund.return_value = {"id": "r1", "payment_id": "p1", "amount": "100.00", "status": "SUCCEEDED"}

    async def _raise(*args, **kwargs):
        raise LLMError("groq timeout")

    fake_llm.complete = _raise

    resp = await client.post(
        "/handle",
        json={
            "conversation_id": "conv-refund-2",
            "user_id": "u1",
            "message": REFUND_MESSAGE[2],
            "intent": "REFUND_REQUEST",
            "user_bearer_token": "tok",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    # Falls back to the original deterministic template text, still carrying
    # the correct rules-engine amount.
    assert body["response_text"] == "Your refund of 100.0 has been processed (Full refund window)."
    mock_refund.assert_awaited_once_with("p1", 100.0, "Full refund window")


async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
