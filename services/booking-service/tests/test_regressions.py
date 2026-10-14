"""
Regression tests for booking-service correctness defects:

  * double booking of an already-confirmed seat
  * expiry loop that never committed its work
  * expiry / TTL-extend touching seat locks owned by another user
  * lock-renewal-failure path whose EXPIRED status was rolled back
  * seat pricing that silently fell back to $0
  * payment transport errors treated as a definite FAILED
  * refund-cancellation of a confirmed booking
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy import select

from app.lock_manager import LockManager
from app.models import Booking, BookingSeat, OutboxEvent, PaymentAttempt
from tests.conftest import auth_headers

pytestmark = pytest.mark.asyncio


def _lock_body(event_id=None, seat_ids=None) -> dict:
    return {
        "event_id": str(event_id or uuid.uuid4()),
        "seat_ids": [str(s) for s in (seat_ids or [uuid.uuid4()])],
    }


def _resp(status_code: int, body: dict | None = None) -> MagicMock:
    resp = MagicMock(status_code=status_code)
    resp.json.return_value = body or {}
    resp.text = str(body)
    return resp


def _payment_client(post=(), get=()) -> AsyncMock:
    """Fake httpx.AsyncClient; `post`/`get` are side_effect sequences."""
    client = AsyncMock()
    client.post.side_effect = list(post)
    client.get.side_effect = list(get)
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False
    return client


def _successful_payment(intent_id: str | None = None) -> AsyncMock:
    intent_id = intent_id or str(uuid.uuid4())
    return _payment_client(
        post=[
            _resp(201, {"id": intent_id, "status": "PENDING"}),
            _resp(200, {"id": intent_id, "status": "SUCCEEDED"}),
        ]
    )


async def _lock(client, user_id, event_id=None, seat_ids=None):
    resp = await client.post(
        "/bookings/lock",
        json=_lock_body(event_id, seat_ids),
        headers=auth_headers(user_id),
    )
    return resp


async def _confirm(client, booking_id, user_id, payment_client):
    with patch("app.booking_service.httpx.AsyncClient", return_value=payment_client):
        return await client.post(
            f"/bookings/{booking_id}/confirm",
            json={"payment_method": "tok_visa"},
            headers=auth_headers(user_id),
        )


# ---------------------------------------------------------------------------
# Double booking
# ---------------------------------------------------------------------------


async def test_confirmed_seat_cannot_be_locked_again(client):
    event_id, seat_id = uuid.uuid4(), uuid.uuid4()
    owner = uuid.uuid4()

    lock = await _lock(client, owner, event_id, [seat_id])
    assert lock.status_code == 201
    confirm = await _confirm(client, lock.json()["id"], owner, _successful_payment())
    assert confirm.json()["status"] == "CONFIRMED"

    second = await _lock(client, uuid.uuid4(), event_id, [seat_id])
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "SEAT_ALREADY_BOOKED"


async def test_internal_endpoint_reports_booked_seats(client):
    event_id, seat_id = uuid.uuid4(), uuid.uuid4()
    owner = uuid.uuid4()
    lock = await _lock(client, owner, event_id, [seat_id])
    await _confirm(client, lock.json()["id"], owner, _successful_payment())

    resp = await client.get(f"/internal/events/{event_id}/locked-seats")
    assert resp.status_code == 200
    assert str(seat_id) in resp.json()["booked_seat_ids"]


# ---------------------------------------------------------------------------
# Expiry loop
# ---------------------------------------------------------------------------


async def _seed_expired_booking(factory, owner: uuid.UUID, event_id, seat_id) -> uuid.UUID:
    past = datetime.now(timezone.utc) - timedelta(minutes=1)
    booking_id = uuid.uuid4()
    async with factory() as db:
        db.add(
            Booking(
                id=booking_id,
                user_id=owner,
                event_id=event_id,
                status="PENDING",
                total_amount=50,
                currency="USD",
                locked_at=past - timedelta(minutes=5),
                expires_at=past,
            )
        )
        db.add(BookingSeat(booking_id=booking_id, seat_id=seat_id, price_at_booking=50))
        await db.commit()
    return booking_id


async def _run_expiry_loop_briefly(monkeypatch, factory, redis):
    import app.main as main

    monkeypatch.setattr(main, "async_session_factory", factory)
    monkeypatch.setattr(main.settings, "expiry_check_interval_seconds", 0)
    main.app.state.redis = redis
    task = asyncio.create_task(main._expiry_loop(main.app))
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_expiry_loop_persists_expired_status(monkeypatch, file_session_factory, fake_redis):
    owner, event_id, seat_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    booking_id = await _seed_expired_booking(file_session_factory, owner, event_id, seat_id)

    await _run_expiry_loop_briefly(monkeypatch, file_session_factory, fake_redis)

    async with file_session_factory() as db:
        booking = await db.get(Booking, booking_id)
        assert booking.status == "EXPIRED"
        events = (await db.execute(select(OutboxEvent))).scalars().all()
        assert [e.event_type for e in events] == ["BOOKING_EXPIRED"]


async def test_expiry_does_not_release_another_users_lock(
    monkeypatch, file_session_factory, fake_redis
):
    owner, other, event_id, seat_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await _seed_expired_booking(file_session_factory, owner, event_id, seat_id)
    # The expired booking's own lock already lapsed and another user grabbed the seat.
    key = f"seat_lock:{event_id}:{seat_id}"
    await fake_redis.set(key, str(other), ex=300)

    await _run_expiry_loop_briefly(monkeypatch, file_session_factory, fake_redis)

    assert await fake_redis.get(key) == str(other)


async def test_extend_lock_ttl_requires_ownership(fake_redis):
    lm = LockManager(fake_redis)
    event_id, seat_id = uuid.uuid4(), uuid.uuid4()
    owner, intruder = uuid.uuid4(), uuid.uuid4()
    await lm.lock_seats(event_id, [seat_id], owner, ttl=30)

    extended = await lm.extend_lock_ttl(event_id, [seat_id], intruder, new_ttl=999)

    assert extended is False
    assert await fake_redis.ttl(f"seat_lock:{event_id}:{seat_id}") <= 30


# ---------------------------------------------------------------------------
# Confirm: lock renewal failure must persist the EXPIRED transition
# ---------------------------------------------------------------------------


async def test_confirm_renewal_failure_persists_expired(
    committing_client, file_session_factory, monkeypatch
):
    owner = uuid.uuid4()
    lock = await _lock(committing_client, owner)
    booking_id = uuid.UUID(lock.json()["id"])
    monkeypatch.setattr(LockManager, "extend_lock_ttl", AsyncMock(return_value=False))

    resp = await _confirm(committing_client, booking_id, owner, _payment_client())
    assert resp.status_code == 409

    async with file_session_factory() as db:
        assert (await db.get(Booking, booking_id)).status == "EXPIRED"
        types = [e.event_type for e in (await db.execute(select(OutboxEvent))).scalars()]
        assert "BOOKING_EXPIRED" in types


# ---------------------------------------------------------------------------
# Seat pricing fails closed
# ---------------------------------------------------------------------------


_BOOKABLE_EVENT = {
    "status": "PUBLISHED",
    "event_date": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
}


def _catalog_client(get_side_effect, event=None) -> AsyncMock:
    """Catalog mock: first GET is the event lookup, then one per seat price."""
    first = event if isinstance(event, Exception) else _resp(200, event or _BOOKABLE_EVENT)
    return _payment_client(get=[first, *get_side_effect])


@pytest.mark.real_pricing
async def test_lock_fails_when_catalog_unavailable(client, fake_redis):
    event_id = uuid.uuid4()
    catalog = _catalog_client([httpx.ConnectError("down")])
    with patch("app.booking_service.httpx.AsyncClient", return_value=catalog):
        resp = await _lock(client, uuid.uuid4(), event_id)

    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "CATALOG_UNAVAILABLE"
    assert [k async for k in fake_redis.scan_iter(f"seat_lock:{event_id}:*")] == []


@pytest.mark.real_pricing
async def test_lock_rejects_seat_unknown_to_catalog(client):
    catalog = _catalog_client([_resp(404, {"error": {"code": "SEAT_NOT_FOUND"}})])
    with patch("app.booking_service.httpx.AsyncClient", return_value=catalog):
        resp = await _lock(client, uuid.uuid4())

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "SEAT_NOT_FOUND"


@pytest.mark.real_pricing
async def test_lock_uses_catalog_price(client):
    seats = [uuid.uuid4(), uuid.uuid4()]
    catalog = _catalog_client([_resp(200, {"price": "12.50"}), _resp(200, {"price": "7.25"})])
    with patch("app.booking_service.httpx.AsyncClient", return_value=catalog):
        resp = await _lock(client, uuid.uuid4(), seat_ids=seats)

    assert resp.status_code == 201
    assert resp.json()["total_amount"] == "19.75"


# ---------------------------------------------------------------------------
# Payment outcome unknown -> never assume FAILED
# ---------------------------------------------------------------------------


async def test_confirm_transport_error_keeps_booking_pending(committing_client, fake_redis):
    client = committing_client
    owner, event_id, seat_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    lock = await _lock(client, owner, event_id, [seat_id])
    booking_id = lock.json()["id"]
    intent_id = str(uuid.uuid4())

    payment = _payment_client(
        post=[_resp(201, {"id": intent_id, "status": "PENDING"}), httpx.ReadTimeout("slow")],
        get=[httpx.ConnectError("down")],
    )
    resp = await _confirm(client, booking_id, owner, payment)

    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "PAYMENT_STATUS_UNKNOWN"
    current = await client.get(f"/bookings/{booking_id}", headers=auth_headers(owner))
    assert current.json()["status"] == "PENDING"
    # Seats stay held for the owner while the payment outcome is unknown.
    assert await fake_redis.get(f"seat_lock:{event_id}:{seat_id}") == str(owner)


async def test_confirm_retry_after_unknown_outcome_uses_real_payment_status(committing_client):
    client = committing_client
    owner = uuid.uuid4()
    lock = await _lock(client, owner)
    booking_id = lock.json()["id"]
    intent_id = str(uuid.uuid4())

    first = _payment_client(
        post=[_resp(201, {"id": intent_id, "status": "PENDING"}), httpx.ReadTimeout("slow")],
        get=[httpx.ConnectError("down")],
    )
    assert (await _confirm(client, booking_id, owner, first)).status_code == 503

    # The charge actually went through; create-intent is idempotent and
    # returns the existing (now SUCCEEDED) intent, so no second charge.
    retry = _payment_client(post=[_resp(201, {"id": intent_id, "status": "SUCCEEDED"})])
    resp = await _confirm(client, booking_id, owner, retry)

    assert resp.status_code == 200
    assert resp.json()["status"] == "CONFIRMED"
    assert retry.post.call_count == 1


async def test_confirm_not_pending_conflict_reads_real_status(client):
    owner = uuid.uuid4()
    lock = await _lock(client, owner)
    intent_id = str(uuid.uuid4())

    payment = _payment_client(
        post=[
            _resp(201, {"id": intent_id, "status": "PENDING"}),
            _resp(409, {"error": {"code": "PAYMENT_NOT_PENDING"}}),
        ],
        get=[_resp(200, {"id": intent_id, "status": "SUCCEEDED"})],
    )
    resp = await _confirm(client, lock.json()["id"], owner, payment)

    assert resp.status_code == 200
    assert resp.json()["status"] == "CONFIRMED"


async def test_confirm_records_single_payment_attempt_across_retries(
    committing_client, file_session_factory
):
    client = committing_client
    owner = uuid.uuid4()
    lock = await _lock(client, owner)
    booking_id = lock.json()["id"]
    intent_id = str(uuid.uuid4())

    await _confirm(
        client,
        booking_id,
        owner,
        _payment_client(
            post=[_resp(201, {"id": intent_id, "status": "PENDING"}), httpx.ReadTimeout("x")],
            get=[httpx.ConnectError("down")],
        ),
    )
    await _confirm(
        client,
        booking_id,
        owner,
        _payment_client(post=[_resp(201, {"id": intent_id, "status": "SUCCEEDED"})]),
    )

    async with file_session_factory() as db:
        attempts = (
            await db.execute(
                select(PaymentAttempt).where(PaymentAttempt.booking_id == uuid.UUID(booking_id))
            )
        ).scalars().all()
    assert len(attempts) == 1
    assert attempts[0].status == "SUCCEEDED"


# ---------------------------------------------------------------------------
# Cancelling a confirmed booking (refund path)
# ---------------------------------------------------------------------------


async def test_cancel_confirmed_booking_frees_seat(client):
    owner, event_id, seat_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    lock = await _lock(client, owner, event_id, [seat_id])
    booking_id = lock.json()["id"]
    await _confirm(client, booking_id, owner, _successful_payment())

    resp = await client.post(f"/bookings/{booking_id}/cancel", headers=auth_headers(owner))
    assert resp.status_code == 200
    assert resp.json()["status"] == "CANCELLED"

    # Idempotent
    again = await client.post(f"/bookings/{booking_id}/cancel", headers=auth_headers(owner))
    assert again.status_code == 200

    relock = await _lock(client, uuid.uuid4(), event_id, [seat_id])
    assert relock.status_code == 201


async def test_cancel_requires_ownership_and_confirmed_status(committing_client):
    client = committing_client
    owner = uuid.uuid4()
    lock = await _lock(client, owner)
    booking_id = lock.json()["id"]

    pending = await client.post(f"/bookings/{booking_id}/cancel", headers=auth_headers(owner))
    assert pending.status_code == 409

    await _confirm(client, booking_id, owner, _successful_payment())
    stranger = await client.post(
        f"/bookings/{booking_id}/cancel", headers=auth_headers(uuid.uuid4())
    )
    assert stranger.status_code == 403


async def test_confirm_fails_without_charging_when_seat_already_sold(
    committing_client, file_session_factory, fake_redis
):
    """If Redis lost a lock (e.g. a restart) and two PENDING bookings hold the
    same seat, the DB reservation constraint stops the second sale before any
    payment call is made."""
    client = committing_client
    event_id, seat_id = uuid.uuid4(), uuid.uuid4()
    first_owner, second_owner = uuid.uuid4(), uuid.uuid4()

    first = await _lock(client, first_owner, event_id, [seat_id])
    await fake_redis.flushall()  # simulate Redis losing every lock
    second = await _lock(client, second_owner, event_id, [seat_id])
    assert second.status_code == 201

    await fake_redis.set(f"seat_lock:{event_id}:{seat_id}", str(first_owner), ex=300)
    confirmed = await _confirm(client, first.json()["id"], first_owner, _successful_payment())
    assert confirmed.json()["status"] == "CONFIRMED"

    await fake_redis.set(f"seat_lock:{event_id}:{seat_id}", str(second_owner), ex=300)
    payment = _payment_client()
    resp = await _confirm(client, second.json()["id"], second_owner, payment)

    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "SEAT_ALREADY_BOOKED"
    payment.post.assert_not_called()
    async with file_session_factory() as db:
        assert (await db.get(Booking, uuid.UUID(second.json()["id"]))).status == "FAILED"


# ---------------------------------------------------------------------------
# Backfill: bookings confirmed before seat_reservations existed
# ---------------------------------------------------------------------------


async def _seed_confirmed_booking(factory, event_id, seat_id) -> uuid.UUID:
    now = datetime.now(timezone.utc)
    booking_id = uuid.uuid4()
    async with factory() as db:
        db.add(
            Booking(
                id=booking_id,
                user_id=uuid.uuid4(),
                event_id=event_id,
                status="CONFIRMED",
                total_amount=50,
                currency="USD",
                locked_at=now,
                expires_at=now,
                confirmed_at=now,
            )
        )
        db.add(BookingSeat(booking_id=booking_id, seat_id=seat_id, price_at_booking=50))
        await db.commit()
    return booking_id


async def test_backfill_reserves_seats_of_previously_confirmed_bookings(
    committing_client, file_session_factory
):
    from app.booking_service import backfill_seat_reservations

    event_id, seat_id, other_seat = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await _seed_confirmed_booking(file_session_factory, event_id, seat_id)
    # Historical data may already contain a double sale; it must not crash startup.
    await _seed_confirmed_booking(file_session_factory, event_id, seat_id)
    await _seed_confirmed_booking(file_session_factory, event_id, other_seat)

    assert await backfill_seat_reservations(file_session_factory) == 2
    assert await backfill_seat_reservations(file_session_factory) == 0  # idempotent

    for seat in (seat_id, other_seat):
        resp = await _lock(committing_client, uuid.uuid4(), event_id, [seat])
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "SEAT_ALREADY_BOOKED"


# ---------------------------------------------------------------------------
# Only PUBLISHED events that haven't started can be booked
# ---------------------------------------------------------------------------


@pytest.mark.real_pricing
@pytest.mark.parametrize(
    "event",
    [
        {"status": "PUBLISHED", "event_date": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()},
        {"status": "DRAFT", "event_date": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()},
        {"status": "CANCELLED", "event_date": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()},
    ],
    ids=["already-started", "draft", "cancelled"],
)
async def test_lock_rejects_event_that_is_not_bookable(client, fake_redis, event):
    event_id = uuid.uuid4()
    catalog = _catalog_client([_resp(200, {"price": "10.00"})], event=event)
    with patch("app.booking_service.httpx.AsyncClient", return_value=catalog):
        resp = await _lock(client, uuid.uuid4(), event_id)

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "EVENT_NOT_BOOKABLE"
    assert [k async for k in fake_redis.scan_iter(f"seat_lock:{event_id}:*")] == []


@pytest.mark.real_pricing
async def test_lock_rejects_unknown_event(client):
    catalog = _payment_client(get=[_resp(404, {"error": {"code": "EVENT_NOT_FOUND"}})])
    with patch("app.booking_service.httpx.AsyncClient", return_value=catalog):
        resp = await _lock(client, uuid.uuid4())

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "EVENT_NOT_FOUND"


@pytest.mark.real_pricing
async def test_event_not_bookable_is_reported_before_seat_already_sold(client):
    """For a finished event, 'event is over' is the meaningful answer even if
    the requested seat was sold back when the event was upcoming."""
    event_id, seat_id, owner = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with patch("app.booking_service._fetch_seat_prices", AsyncMock(return_value={seat_id: 50})):
        lock = await _lock(client, owner, event_id, [seat_id])
        await _confirm(client, lock.json()["id"], owner, _successful_payment())

    past = {"status": "PUBLISHED", "event_date": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()}
    with patch("app.booking_service.httpx.AsyncClient", return_value=_catalog_client([], event=past)):
        resp = await _lock(client, uuid.uuid4(), event_id, [seat_id])

    assert resp.json()["error"]["code"] == "EVENT_NOT_BOOKABLE"
