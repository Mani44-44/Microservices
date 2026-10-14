"""
Reconciling bookings whose payment outcome was unknown.

confirm_booking() leaves a booking PENDING with a PaymentAttempt of status
UNKNOWN when it can't learn whether the charge went through. Each expiry
pass (run_expiry_once) must first ask payment-service what really happened,
so a customer who was charged is never left with an expired booking.
"""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy import select

from app.booking_service import run_expiry_once
from app.lock_manager import LockManager
from app.models import Booking, BookingSeat, OutboxEvent, PaymentAttempt, SeatReservation

pytestmark = pytest.mark.asyncio


def _resp(status_code: int, body: dict | None = None) -> MagicMock:
    resp = MagicMock(status_code=status_code)
    resp.json.return_value = body or {}
    return resp


def _payment_service(get=(), post=()) -> AsyncMock:
    client = AsyncMock()
    client.get.side_effect = list(get)
    client.post.side_effect = list(post)
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False
    return client


async def _seed(factory, redis, *, status: str, expired: bool, attempt_status: str = "UNKNOWN"):
    now = datetime.now(timezone.utc)
    owner, event_id, seat_id, booking_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    expires_at = now - timedelta(minutes=1) if expired else now + timedelta(minutes=4)
    async with factory() as db:
        db.add(
            Booking(
                id=booking_id, user_id=owner, event_id=event_id, status=status,
                total_amount=50, currency="USD", locked_at=now - timedelta(minutes=1), expires_at=expires_at,
            )
        )
        db.add(BookingSeat(booking_id=booking_id, seat_id=seat_id, price_at_booking=50))
        db.add(
            PaymentAttempt(
                booking_id=booking_id, payment_intent_id="pi_1", amount=50,
                status=attempt_status, idempotency_key=str(booking_id),
            )
        )
        await db.commit()
    if not expired:
        await redis.set(f"seat_lock:{event_id}:{seat_id}", str(owner), ex=240)
    return {"booking_id": booking_id, "owner": owner, "event_id": event_id, "seat_id": seat_id}


async def _state(factory, booking_id):
    async with factory() as db:
        booking = await db.get(Booking, booking_id)
        attempt = (
            await db.execute(select(PaymentAttempt).where(PaymentAttempt.booking_id == booking_id))
        ).scalar_one()
        reservations = (
            await db.execute(select(SeatReservation).where(SeatReservation.booking_id == booking_id))
        ).scalars().all()
        events = [e.event_type for e in (await db.execute(select(OutboxEvent))).scalars()]
        return booking.status, attempt.status, len(reservations), events


async def _run(factory, redis, payment_service):
    with patch("app.booking_service.httpx.AsyncClient", return_value=payment_service):
        await run_expiry_once(factory, LockManager(redis))


async def test_pending_booking_with_succeeded_payment_is_confirmed(file_session_factory, fake_redis):
    b = await _seed(file_session_factory, fake_redis, status="PENDING", expired=False)

    await _run(file_session_factory, fake_redis, _payment_service(get=[_resp(200, {"status": "SUCCEEDED"})]))

    assert await _state(file_session_factory, b["booking_id"]) == ("CONFIRMED", "SUCCEEDED", 1, ["BOOKING_CONFIRMED"])
    assert await fake_redis.get(f"seat_lock:{b['event_id']}:{b['seat_id']}") is None


async def test_pending_booking_with_failed_payment_is_failed(file_session_factory, fake_redis):
    b = await _seed(file_session_factory, fake_redis, status="PENDING", expired=False)

    await _run(file_session_factory, fake_redis, _payment_service(get=[_resp(200, {"status": "FAILED"})]))

    assert await _state(file_session_factory, b["booking_id"]) == ("FAILED", "FAILED", 0, ["BOOKING_FAILED"])
    assert await fake_redis.get(f"seat_lock:{b['event_id']}:{b['seat_id']}") is None


async def test_expired_booking_paid_late_is_confirmed_when_seat_still_free(file_session_factory, fake_redis):
    b = await _seed(file_session_factory, fake_redis, status="EXPIRED", expired=True)

    await _run(file_session_factory, fake_redis, _payment_service(get=[_resp(200, {"status": "SUCCEEDED"})]))

    status, attempt, reservations, events = await _state(file_session_factory, b["booking_id"])
    assert (status, attempt, reservations) == ("CONFIRMED", "SUCCEEDED", 1)
    assert "BOOKING_CONFIRMED" in events


async def test_expired_booking_paid_late_is_refunded_when_seat_was_sold(file_session_factory, fake_redis):
    b = await _seed(file_session_factory, fake_redis, status="EXPIRED", expired=True)
    async with file_session_factory() as db:  # someone else bought the seat meanwhile
        other = uuid.uuid4()
        db.add(Booking(id=other, user_id=uuid.uuid4(), event_id=b["event_id"], status="CONFIRMED",
                       total_amount=50, currency="USD", expires_at=datetime.now(timezone.utc)))
        db.add(SeatReservation(event_id=b["event_id"], seat_id=b["seat_id"], booking_id=other))
        await db.commit()
    payment_service = _payment_service(
        get=[_resp(200, {"status": "SUCCEEDED"})], post=[_resp(201, {"status": "SUCCEEDED"})]
    )

    await _run(file_session_factory, fake_redis, payment_service)

    status, attempt, reservations, events = await _state(file_session_factory, b["booking_id"])
    assert (status, attempt, reservations) == ("FAILED", "REFUNDED", 0)
    assert "BOOKING_FAILED" in events
    url = payment_service.post.call_args.args[0]
    assert url.endswith("/payments/pi_1/refund")
    assert payment_service.post.call_args.kwargs["json"]["amount"] == "50.00"


async def test_refund_failure_is_retried_on_the_next_pass(file_session_factory, fake_redis):
    b = await _seed(file_session_factory, fake_redis, status="FAILED", expired=True, attempt_status="REFUND_PENDING")

    await _run(file_session_factory, fake_redis, _payment_service(post=[httpx.ConnectError("down")]))
    assert (await _state(file_session_factory, b["booking_id"]))[1] == "REFUND_PENDING"

    await _run(file_session_factory, fake_redis, _payment_service(post=[_resp(201, {})]))
    assert (await _state(file_session_factory, b["booking_id"]))[1] == "REFUNDED"


async def test_still_unknown_payment_leaves_booking_alone(file_session_factory, fake_redis):
    b = await _seed(file_session_factory, fake_redis, status="PENDING", expired=False)

    await _run(file_session_factory, fake_redis, _payment_service(get=[httpx.ConnectError("down")]))

    assert await _state(file_session_factory, b["booking_id"]) == ("PENDING", "UNKNOWN", 0, [])
    assert await fake_redis.get(f"seat_lock:{b['event_id']}:{b['seat_id']}") == str(b["owner"])
