"""
booking_service.py
~~~~~~~~~~~~~~~~~~
Core booking business logic.

Concurrency safety strategy:
  1. Seat locks are held in Redis via atomic Lua scripts (LockManager). Every
     lock operation after the initial acquire (extend, release) checks that
     the lock is still owned by the booking's user.
  2. A sold seat is recorded in `seat_reservations`, whose UNIQUE(event_id,
     seat_id) constraint is the database-level guarantee against selling the
     same seat twice — Redis locks are released once a booking is confirmed.
  3. DB writes use PostgreSQL transactions; row-level locking (`SELECT FOR UPDATE`)
     is applied when reading a booking for mutation to prevent double-processing.
  4. The expiry handler releases locks AND updates DB status within a
     transaction it commits, then emits an outbox event.
"""
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Optional, Tuple

import httpx
import structlog
from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from libs.security import internal_headers
from app.lock_manager import LockManager
from app.models import Booking, BookingSeat, OutboxEvent, PaymentAttempt, SeatReservation
from app.schemas import ConfirmBookingRequest, LockSeatsRequest

log = structlog.get_logger()
settings = get_settings()

_PAYMENT_SUCCEEDED = {"SUCCEEDED", "SUCCESS", "COMPLETED", "APPROVED"}
_PAYMENT_FAILED = {"FAILED", "DECLINED", "CANCELLED", "REFUNDED"}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _error(status_code: int, code: str, message: str, details: dict | None = None) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail={"error": {"code": code, "message": message, "details": details or {}}},
    )


def _booking_payload(booking: Booking) -> dict:
    return {
        "booking_id": str(booking.id),
        "user_id": str(booking.user_id),
        "event_id": str(booking.event_id),
        "status": booking.status,
        "total_amount": str(booking.total_amount),
        "currency": booking.currency,
    }


async def _get_seat_price(
    http_client: httpx.AsyncClient,
    event_id: uuid.UUID,
    seat_id: uuid.UUID,
) -> Decimal:
    """Fetch a single seat's price from the catalog service.

    Fails closed: a seat the catalog doesn't know is rejected (422), and an
    unreachable/erroring catalog aborts the booking (503) rather than pricing
    the seat at zero.
    """
    url = f"{settings.catalog_service_url}/events/{event_id}/seats/{seat_id}"
    try:
        resp = await http_client.get(url, params={"include_status": "false"}, timeout=5.0)
    except httpx.HTTPError as exc:
        log.warning("catalog_service_unavailable", error=str(exc), seat_id=str(seat_id))
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "CATALOG_UNAVAILABLE",
            "Seat pricing is temporarily unavailable; please try again",
        )

    if resp.status_code == 200:
        # catalog service returns {'price': '99.99', ...}
        return Decimal(str(resp.json()["price"]))

    log.warning(
        "catalog_seat_lookup_failed",
        event_id=str(event_id),
        seat_id=str(seat_id),
        status_code=resp.status_code,
    )
    if resp.status_code == 404:
        raise _error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "SEAT_NOT_FOUND",
            "One or more seats do not exist for this event",
            {"seat_id": str(seat_id)},
        )
    raise _error(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "CATALOG_UNAVAILABLE",
        "Seat pricing is temporarily unavailable; please try again",
    )


async def _check_event_bookable(http_client: httpx.AsyncClient, event_id: uuid.UUID) -> None:
    """Only PUBLISHED events that haven't started yet can be booked."""
    try:
        resp = await http_client.get(f"{settings.catalog_service_url}/events/{event_id}", timeout=5.0)
    except httpx.HTTPError as exc:
        log.warning("catalog_service_unavailable", error=str(exc), event_id=str(event_id))
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "CATALOG_UNAVAILABLE",
            "Event details are temporarily unavailable; please try again",
        )
    if resp.status_code == 404:
        raise _error(status.HTTP_404_NOT_FOUND, "EVENT_NOT_FOUND", f"Event {event_id} not found")
    if resp.status_code != 200:
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "CATALOG_UNAVAILABLE",
            "Event details are temporarily unavailable; please try again",
        )

    event = resp.json()
    event_date = datetime.fromisoformat(str(event["event_date"]).replace("Z", "+00:00"))
    if event_date.tzinfo is None:
        event_date = event_date.replace(tzinfo=timezone.utc)
    if event.get("status") != "PUBLISHED" or event_date <= _now():
        reason = "has already started" if event_date <= _now() else f"is {str(event.get('status')).lower()}"
        raise _error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "EVENT_NOT_BOOKABLE",
            f"This event {reason} and can't be booked",
            {"status": event.get("status"), "event_date": event_date.isoformat()},
        )


async def _fetch_seat_prices(
    event_id: uuid.UUID,
    seat_ids: List[uuid.UUID],
) -> dict[uuid.UUID, Decimal]:
    """Check the event is bookable, then fetch seat prices concurrently."""
    async with httpx.AsyncClient(headers=internal_headers()) as client:
        await _check_event_bookable(client, event_id)
        prices = await asyncio.gather(
            *(_get_seat_price(client, event_id, seat_id) for seat_id in seat_ids)
        )
    return dict(zip(seat_ids, prices))


async def _booked_seat_ids(
    db: AsyncSession,
    event_id: uuid.UUID,
    seat_ids: Optional[List[uuid.UUID]] = None,
) -> set[uuid.UUID]:
    """Seats of *event_id* already sold to a confirmed booking."""
    query = select(SeatReservation.seat_id).where(SeatReservation.event_id == event_id)
    if seat_ids is not None:
        query = query.where(SeatReservation.seat_id.in_(seat_ids))
    result = await db.execute(query)
    return set(result.scalars().all())


async def get_booked_seat_ids(db: AsyncSession, event_id: uuid.UUID) -> List[str]:
    return sorted(str(s) for s in await _booked_seat_ids(db, event_id))


async def _fetch_booking_with_lock(
    booking_id: uuid.UUID,
    db: AsyncSession,
) -> Booking:
    """Load a booking row with SELECT FOR UPDATE to prevent concurrent mutations."""
    result = await db.execute(
        select(Booking)
        .options(selectinload(Booking.seats))
        .where(Booking.id == booking_id)
        .with_for_update()
    )
    booking = result.scalar_one_or_none()
    if booking is None:
        raise _error(
            status.HTTP_404_NOT_FOUND,
            "BOOKING_NOT_FOUND",
            f"Booking {booking_id} not found",
        )
    return booking


def _check_owner(booking: Booking, user_id: uuid.UUID) -> None:
    if booking.user_id != user_id:
        raise _error(status.HTTP_403_FORBIDDEN, "FORBIDDEN", "You do not own this booking")


# ---------------------------------------------------------------------------
# 1. Lock seats
# ---------------------------------------------------------------------------


async def lock_seats(
    request: LockSeatsRequest,
    user_id: uuid.UUID,
    db: AsyncSession,
    lock_manager: LockManager,
) -> Booking:
    """Lock seats in Redis and create a PENDING booking record.

    Steps:
      1. Deduplicate seat_ids.
      2. Check the event is bookable and fetch prices from catalog (fails closed).
      3. Reject seats already sold to a confirmed booking.
      4. Atomically lock all seats in Redis (all-or-nothing).
      5. Persist Booking + BookingSeat rows + OutboxEvent in one transaction;
         if that fails, release the Redis locks rather than leak them.
    """
    # Deduplicate while preserving order
    unique_seat_ids: List[uuid.UUID] = list(dict.fromkeys(request.seat_ids))

    if len(unique_seat_ids) > settings.max_seats_per_booking:
        raise _error(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "TOO_MANY_SEATS",
            f"Cannot book more than {settings.max_seats_per_booking} seats at once",
            {"max": settings.max_seats_per_booking},
        )

    log.info(
        "lock_seats_start",
        user_id=str(user_id),
        event_id=str(request.event_id),
        seat_count=len(unique_seat_ids),
    )

    # Event bookability + prices first: for a finished event "event is over"
    # is the meaningful answer, even if a requested seat was sold back then.
    prices = await _fetch_seat_prices(request.event_id, unique_seat_ids)

    booked = await _booked_seat_ids(db, request.event_id, unique_seat_ids)
    if booked:
        raise _error(
            status.HTTP_409_CONFLICT,
            "SEAT_ALREADY_BOOKED",
            "One or more seats have already been sold",
            {"booked_seat_ids": sorted(str(s) for s in booked)},
        )

    # Atomically lock all seats
    success, failed_idx = await lock_manager.lock_seats(
        event_id=request.event_id,
        seat_ids=unique_seat_ids,
        user_id=user_id,
        ttl=settings.seat_lock_ttl_seconds,
    )

    if not success:
        # failed_idx is 1-based
        failed_seat_id = unique_seat_ids[failed_idx - 1] if failed_idx > 0 else None
        raise _error(
            status.HTTP_409_CONFLICT,
            "SEAT_ALREADY_LOCKED",
            "One or more seats are already reserved by another user",
            {"failed_seat_id": str(failed_seat_id) if failed_seat_id else None},
        )

    try:
        booking = await _persist_pending_booking(
            db, request.event_id, unique_seat_ids, user_id, prices
        )
    except Exception:
        await lock_manager.release_seats(
            event_id=request.event_id, seat_ids=unique_seat_ids, user_id=user_id
        )
        raise

    log.info(
        "booking_created",
        booking_id=str(booking.id),
        user_id=str(user_id),
        event_id=str(request.event_id),
        total_amount=str(booking.total_amount),
    )
    return booking


async def _persist_pending_booking(
    db: AsyncSession,
    event_id: uuid.UUID,
    seat_ids: List[uuid.UUID],
    user_id: uuid.UUID,
    prices: dict[uuid.UUID, Decimal],
) -> Booking:
    now = _now()
    expires_at = now + timedelta(seconds=settings.seat_lock_ttl_seconds)

    booking = Booking(
        id=uuid.uuid4(),
        user_id=user_id,
        event_id=event_id,
        status="PENDING",
        total_amount=sum(prices.values(), Decimal("0.00")),
        currency="USD",
        locked_at=now,
        expires_at=expires_at,
    )
    db.add(booking)
    await db.flush()  # get booking.id without committing

    for seat_id in seat_ids:
        db.add(
            BookingSeat(
                id=uuid.uuid4(),
                booking_id=booking.id,
                seat_id=seat_id,
                price_at_booking=prices[seat_id],
            )
        )

    db.add(
        OutboxEvent(
            id=uuid.uuid4(),
            event_type="SEATS_LOCKED",
            payload={
                **_booking_payload(booking),
                "seat_ids": [str(s) for s in seat_ids],
                "expires_at": expires_at.isoformat(),
            },
        )
    )

    await db.flush()
    await db.refresh(booking, ["seats"])
    return booking


# ---------------------------------------------------------------------------
# 2. Confirm booking
# ---------------------------------------------------------------------------


async def _read_payment_status(client: httpx.AsyncClient, payment_intent_id: str) -> Optional[str]:
    """Ask payment-service for an intent's real status; None if still unknown."""
    try:
        resp = await client.get(f"{settings.payment_service_url}/payments/{payment_intent_id}")
    except httpx.HTTPError as exc:
        log.warning("payment_status_read_failed", payment_intent_id=payment_intent_id, error=str(exc))
        return None
    if resp.status_code != 200:
        return None
    payment_status = str(resp.json().get("status", "")).upper()
    return payment_status if payment_status in _PAYMENT_SUCCEEDED | _PAYMENT_FAILED else None


async def _charge(
    booking: Booking, request: ConfirmBookingRequest
) -> Tuple[Optional[str], Optional[str]]:
    """Charge for *booking* via payment-service.

    Returns (payment_status, payment_intent_id) where payment_status is
    "SUCCEEDED", "FAILED", or None when the outcome is unknown (the confirm
    call errored and payment-service couldn't tell us what happened).

    The booking id is the payment idempotency key, so create-intent returns
    the existing intent on a retry — if a previous attempt already charged
    the card, its SUCCEEDED status is reused instead of charging again.

    Raises 502 if no intent could be created (no charge can have happened).
    """
    async with httpx.AsyncClient(timeout=30.0, headers=internal_headers()) as client:
        try:
            create_resp = await client.post(
                f"{settings.payment_service_url}/payments/create-intent",
                json={
                    "booking_id": str(booking.id),
                    "amount": str(booking.total_amount),
                    "currency": booking.currency,
                    "idempotency_key": str(booking.id),
                    "user_id": str(booking.user_id),
                },
            )
        except httpx.HTTPError as exc:
            log.error("payment_create_intent_error", booking_id=str(booking.id), error=str(exc))
            create_resp = None

        if create_resp is None or create_resp.status_code not in (200, 201):
            log.error(
                "payment_create_intent_failed",
                booking_id=str(booking.id),
                status_code=getattr(create_resp, "status_code", None),
            )
            raise _error(
                status.HTTP_502_BAD_GATEWAY,
                "PAYMENT_SERVICE_ERROR",
                "Failed to create payment intent",
            )

        intent_data = create_resp.json()
        payment_intent_id = intent_data.get("id") or intent_data.get("payment_intent_id")
        intent_status = str(intent_data.get("status") or "PENDING").upper()
        if intent_status in _PAYMENT_SUCCEEDED:
            return "SUCCEEDED", payment_intent_id
        if intent_status in _PAYMENT_FAILED:
            return "FAILED", payment_intent_id

        try:
            confirm_resp = await client.post(
                f"{settings.payment_service_url}/payments/{payment_intent_id}/confirm",
                json={"payment_method_details": request.payment_method},
            )
        except httpx.HTTPError as exc:
            log.error(
                "payment_confirm_error",
                booking_id=str(booking.id),
                payment_intent_id=payment_intent_id,
                error=str(exc),
            )
            confirm_resp = None

        if confirm_resp is not None and confirm_resp.status_code in (200, 201):
            confirmed = str(confirm_resp.json().get("status", "SUCCEEDED")).upper()
            return ("SUCCEEDED" if confirmed in _PAYMENT_SUCCEEDED else "FAILED"), payment_intent_id

        if confirm_resp is not None and 400 <= confirm_resp.status_code < 500 and confirm_resp.status_code != 409:
            # A definite rejection: the charge was never attempted.
            log.error(
                "payment_confirm_rejected",
                booking_id=str(booking.id),
                payment_intent_id=payment_intent_id,
                status_code=confirm_resp.status_code,
            )
            return "FAILED", payment_intent_id

        # Transport error, 5xx, or 409 (intent no longer PENDING): the charge
        # may or may not have happened, so ask instead of assuming.
        payment_status = await _read_payment_status(client, payment_intent_id)
        if payment_status is None:
            return None, payment_intent_id
        return ("SUCCEEDED" if payment_status in _PAYMENT_SUCCEEDED else "FAILED"), payment_intent_id


async def _record_payment_attempt(
    db: AsyncSession,
    booking: Booking,
    payment_intent_id: Optional[str],
    payment_status: str,
) -> None:
    """Upsert the booking's payment attempt (one per idempotency key)."""
    idempotency_key = str(booking.id)
    result = await db.execute(
        select(PaymentAttempt).where(PaymentAttempt.idempotency_key == idempotency_key)
    )
    attempt = result.scalar_one_or_none()
    if attempt is None:
        db.add(
            PaymentAttempt(
                id=uuid.uuid4(),
                booking_id=booking.id,
                payment_intent_id=payment_intent_id,
                amount=booking.total_amount,
                status=payment_status,
                idempotency_key=idempotency_key,
            )
        )
    else:
        attempt.payment_intent_id = payment_intent_id or attempt.payment_intent_id
        attempt.status = payment_status


async def _release_reservations(db: AsyncSession, booking_id: uuid.UUID) -> None:
    await db.execute(delete(SeatReservation).where(SeatReservation.booking_id == booking_id))


async def confirm_booking(
    booking_id: uuid.UUID,
    user_id: uuid.UUID,
    request: ConfirmBookingRequest,
    db: AsyncSession,
    lock_manager: LockManager,
) -> Booking:
    """Charge the user and confirm the booking.

    Steps:
      1. Load booking with row-lock.
      2. Ownership + status checks.
      3. Idempotency – if already CONFIRMED, return immediately.
      4. Extend seat lock TTL for payment processing time (owner-checked).
      5. Reserve the seats in the DB *before* charging, so a seat that was
         somehow sold already fails here without charging anyone.
      6. Call payment service to create and confirm a payment intent.
      7. Update booking status and release Redis locks.
      8. Emit outbox event.
    """
    booking = await _fetch_booking_with_lock(booking_id, db)
    _check_owner(booking, user_id)

    # Idempotency: already confirmed
    if booking.status == "CONFIRMED":
        log.info("confirm_booking_idempotent", booking_id=str(booking_id))
        return booking

    # Must be PENDING
    if booking.status != "PENDING":
        raise _error(
            status.HTTP_409_CONFLICT,
            "INVALID_BOOKING_STATUS",
            f"Cannot confirm a booking with status '{booking.status}'",
            {"current_status": booking.status},
        )

    # Expiry check
    # SQLite (used in tests) doesn't round-trip tzinfo like Postgres does,
    # so a freshly-fetched expires_at can come back naive.
    expires_at = booking.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= _now():
        raise _error(
            status.HTTP_409_CONFLICT,
            "BOOKING_EXPIRED",
            "Seat lock has expired; please start a new booking",
            {"expired_at": booking.expires_at.isoformat()},
        )

    seat_ids = [s.seat_id for s in booking.seats]

    # Extend TTL so locks survive payment processing. A failed extend means
    # at least one seat lock expired or now belongs to another user —
    # nothing to retry, so abort before contacting payment.
    renewed = await lock_manager.extend_lock_ttl(
        event_id=booking.event_id,
        seat_ids=seat_ids,
        user_id=user_id,
        new_ttl=settings.seat_lock_ttl_seconds + 120,
    )
    if not renewed:
        await lock_manager.release_seats(
            event_id=booking.event_id,
            seat_ids=seat_ids,
            user_id=user_id,
        )
        booking.status = "EXPIRED"
        booking.updated_at = _now()
        db.add(
            OutboxEvent(
                id=uuid.uuid4(),
                event_type="BOOKING_EXPIRED",
                payload={
                    **_booking_payload(booking),
                    "expired_at": booking.updated_at.isoformat(),
                },
            )
        )
        # get_db only commits on the success path; this route raises next,
        # so commit explicitly or the EXPIRED transition is rolled back.
        await db.commit()
        raise _error(
            status.HTTP_409_CONFLICT,
            "BOOKING_EXPIRED",
            "Seat lock has expired; please start a new booking",
        )

    try:
        async with db.begin_nested():
            for seat_id in seat_ids:
                db.add(
                    SeatReservation(
                        event_id=booking.event_id, seat_id=seat_id, booking_id=booking.id
                    )
                )
    except IntegrityError:
        log.error("seat_already_reserved", booking_id=str(booking.id))
        await lock_manager.release_seats(
            event_id=booking.event_id, seat_ids=seat_ids, user_id=user_id
        )
        booking.status = "FAILED"
        booking.updated_at = _now()
        db.add(
            OutboxEvent(
                id=uuid.uuid4(),
                event_type="BOOKING_FAILED",
                payload={**_booking_payload(booking), "reason": "SEAT_ALREADY_BOOKED"},
            )
        )
        await db.commit()
        raise _error(
            status.HTTP_409_CONFLICT,
            "SEAT_ALREADY_BOOKED",
            "One or more seats have already been sold",
        )

    try:
        payment_status, payment_intent_id = await _charge(booking, request)
    except Exception:
        await _release_reservations(db, booking.id)
        raise

    if payment_status is None:
        # Outcome unknown: keep the booking PENDING and the seats held so a
        # retry (same idempotency key) can pick up the real result.
        await _release_reservations(db, booking.id)
        await _record_payment_attempt(db, booking, payment_intent_id, "UNKNOWN")
        await db.commit()
        log.error(
            "payment_status_unknown",
            booking_id=str(booking.id),
            payment_intent_id=payment_intent_id,
        )
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "PAYMENT_STATUS_UNKNOWN",
            "We could not confirm your payment status; please retry shortly",
            {"retryable": True},
        )

    await _record_payment_attempt(db, booking, payment_intent_id, payment_status)

    now = _now()

    # Seats are now either committed in the DB or being given up; either
    # way the Redis hold is no longer needed.
    await lock_manager.release_seats(
        event_id=booking.event_id,
        seat_ids=seat_ids,
        user_id=user_id,
    )

    if payment_status == "SUCCEEDED":
        booking.status = "CONFIRMED"
        booking.confirmed_at = now
        booking.updated_at = now
        db.add(
            OutboxEvent(
                id=uuid.uuid4(),
                event_type="BOOKING_CONFIRMED",
                payload=_booking_payload(booking),
            )
        )
        log.info(
            "booking_confirmed",
            booking_id=str(booking.id),
            user_id=str(user_id),
            payment_intent_id=payment_intent_id,
        )
    else:
        await _release_reservations(db, booking.id)
        booking.status = "FAILED"
        booking.updated_at = now
        db.add(
            OutboxEvent(
                id=uuid.uuid4(),
                event_type="BOOKING_FAILED",
                payload={**_booking_payload(booking), "payment_status": payment_status},
            )
        )
        log.warning(
            "booking_payment_failed",
            booking_id=str(booking.id),
            user_id=str(user_id),
            payment_status=payment_status,
        )

    await db.flush()
    await db.refresh(booking, ["seats"])
    return booking


# ---------------------------------------------------------------------------
# 3. Release booking
# ---------------------------------------------------------------------------


async def release_booking(
    booking_id: uuid.UUID,
    user_id: uuid.UUID,
    db: AsyncSession,
    lock_manager: LockManager,
) -> Booking:
    """Cancel a PENDING booking and release Redis seat locks."""
    booking = await _fetch_booking_with_lock(booking_id, db)
    _check_owner(booking, user_id)

    if booking.status != "PENDING":
        raise _error(
            status.HTTP_409_CONFLICT,
            "INVALID_BOOKING_STATUS",
            f"Cannot release a booking with status '{booking.status}'",
            {"current_status": booking.status},
        )

    seat_ids = [s.seat_id for s in booking.seats]

    await lock_manager.release_seats(
        event_id=booking.event_id,
        seat_ids=seat_ids,
        user_id=user_id,
    )

    now = _now()
    booking.status = "CANCELLED"
    booking.cancelled_at = now
    booking.updated_at = now

    db.add(
        OutboxEvent(
            id=uuid.uuid4(),
            event_type="BOOKING_CANCELLED",
            payload=_booking_payload(booking),
        )
    )

    await db.flush()
    await db.refresh(booking, ["seats"])

    log.info("booking_cancelled", booking_id=str(booking_id), user_id=str(user_id))
    return booking


# ---------------------------------------------------------------------------
# 4. Cancel a confirmed booking (refund path)
# ---------------------------------------------------------------------------


async def cancel_confirmed_booking(
    booking_id: uuid.UUID,
    user_id: uuid.UUID,
    db: AsyncSession,
) -> Booking:
    """Cancel a CONFIRMED booking and put its seats back on sale.

    Called by the support agent before it issues a refund, so a refunded
    customer never keeps a valid ticket. Idempotent: an already-cancelled
    booking is returned unchanged.
    """
    booking = await _fetch_booking_with_lock(booking_id, db)
    _check_owner(booking, user_id)

    if booking.status == "CANCELLED":
        log.info("cancel_booking_idempotent", booking_id=str(booking_id))
        return booking

    if booking.status != "CONFIRMED":
        raise _error(
            status.HTTP_409_CONFLICT,
            "INVALID_BOOKING_STATUS",
            f"Only confirmed bookings can be cancelled; current status '{booking.status}'",
            {"current_status": booking.status},
        )

    await _release_reservations(db, booking.id)

    now = _now()
    booking.status = "CANCELLED"
    booking.cancelled_at = now
    booking.updated_at = now
    db.add(
        OutboxEvent(
            id=uuid.uuid4(),
            event_type="BOOKING_CANCELLED",
            payload={**_booking_payload(booking), "was_confirmed": True},
        )
    )

    await db.flush()
    await db.refresh(booking, ["seats"])

    log.info("confirmed_booking_cancelled", booking_id=str(booking_id), user_id=str(user_id))
    return booking


# ---------------------------------------------------------------------------
# 5. Get single booking
# ---------------------------------------------------------------------------


async def get_booking(
    booking_id: uuid.UUID,
    user_id: uuid.UUID,
    db: AsyncSession,
) -> Booking:
    """Fetch a booking with seat details, checking ownership."""
    result = await db.execute(
        select(Booking)
        .options(selectinload(Booking.seats))
        .where(Booking.id == booking_id)
    )
    booking = result.scalar_one_or_none()

    if booking is None:
        raise _error(
            status.HTTP_404_NOT_FOUND,
            "BOOKING_NOT_FOUND",
            f"Booking {booking_id} not found",
        )

    _check_owner(booking, user_id)
    return booking


# ---------------------------------------------------------------------------
# 6. Get paginated user bookings
# ---------------------------------------------------------------------------


async def get_user_bookings(
    user_id: uuid.UUID,
    page: int,
    size: int,
    db: AsyncSession,
) -> Tuple[List[Booking], int]:
    """Return paginated bookings for a user, newest first."""
    offset = (page - 1) * size

    # Total count
    count_result = await db.execute(
        select(func.count(Booking.id)).where(Booking.user_id == user_id)
    )
    total = count_result.scalar_one()

    # Paginated rows with seats eager-loaded
    rows_result = await db.execute(
        select(Booking)
        .options(selectinload(Booking.seats))
        .where(Booking.user_id == user_id)
        .order_by(Booking.created_at.desc())
        .offset(offset)
        .limit(size)
    )
    bookings = list(rows_result.scalars().all())

    return bookings, total


# ---------------------------------------------------------------------------
# 7. Expiry handler (background task)
# ---------------------------------------------------------------------------


async def expire_pending_bookings(
    db: AsyncSession,
    lock_manager: LockManager,
) -> int:
    """Find PENDING bookings past their expiry time and mark them EXPIRED.

    Does not commit; see run_expiry_once().

    Returns:
        Number of bookings expired in this run.
    """
    now = _now()

    # Load expired PENDING bookings (with seat data for lock release)
    result = await db.execute(
        select(Booking)
        .options(selectinload(Booking.seats), selectinload(Booking.payment_attempts))
        .where(Booking.status == "PENDING")
        .where(Booking.expires_at <= now)
        .with_for_update(skip_locked=True)  # avoid contention with confirm path
    )
    expired_bookings = list(result.scalars().all())

    if not expired_bookings:
        return 0

    expired_count = 0
    for booking in expired_bookings:
        try:
            seat_ids = [s.seat_id for s in booking.seats]

            # Only release locks still held by this booking's user: once a
            # lock's TTL lapses another user may legitimately hold the seat.
            if seat_ids:
                await lock_manager.release_seats(
                    event_id=booking.event_id,
                    seat_ids=seat_ids,
                    user_id=booking.user_id,
                )

            booking.status = "EXPIRED"
            booking.updated_at = now

            payload = {**_booking_payload(booking), "expired_at": now.isoformat()}
            if any(a.status == "UNKNOWN" for a in booking.payment_attempts):
                # A charge may have gone through; flag it for reconciliation.
                payload["payment_status_unknown"] = True
                log.error("expired_booking_payment_unknown", booking_id=str(booking.id))

            db.add(OutboxEvent(id=uuid.uuid4(), event_type="BOOKING_EXPIRED", payload=payload))
            expired_count += 1

        except Exception as exc:
            log.error(
                "expire_booking_error",
                booking_id=str(booking.id),
                error=str(exc),
            )

    await db.flush()

    log.info("bookings_expired", count=expired_count, checked_at=now.isoformat())
    return expired_count


# ---------------------------------------------------------------------------
# 8. Reconcile payments whose outcome was unknown
# ---------------------------------------------------------------------------

_RECONCILE_STATUSES = ("UNKNOWN", "REFUND_PENDING")


async def _refund_attempt(
    client: httpx.AsyncClient, booking: Booking, attempt: PaymentAttempt
) -> None:
    """Refund a charge for a seat we could not deliver. On any failure the
    attempt stays REFUND_PENDING and the next pass retries."""
    amount = Decimal(str(booking.total_amount)).quantize(Decimal("0.01"))
    try:
        resp = await client.post(
            f"{settings.payment_service_url}/payments/{attempt.payment_intent_id}/refund",
            json={"amount": str(amount), "reason": "Seat no longer available after delayed payment"},
        )
    except httpx.HTTPError as exc:
        log.error("reconcile_refund_failed", booking_id=str(booking.id), error=str(exc))
        return
    # 409: the payment is no longer refundable, i.e. it was already refunded.
    if resp.status_code in (200, 201, 409):
        attempt.status = "REFUNDED"
        log.warning("reconcile_payment_refunded", booking_id=str(booking.id), amount=str(amount))
    else:
        log.error("reconcile_refund_failed", booking_id=str(booking.id), status_code=resp.status_code)


async def _reconcile_one(
    db: AsyncSession, client: httpx.AsyncClient, lock_manager: LockManager, booking_id: uuid.UUID
) -> bool:
    result = await db.execute(
        select(Booking)
        .options(selectinload(Booking.seats), selectinload(Booking.payment_attempts))
        .where(Booking.id == booking_id)
        .with_for_update(skip_locked=True)  # a concurrent confirm owns it
    )
    booking = result.scalar_one_or_none()
    if booking is None:
        return False
    attempt = next((a for a in booking.payment_attempts if a.status in _RECONCILE_STATUSES), None)
    if attempt is None:
        return False

    if attempt.status == "REFUND_PENDING":
        await _refund_attempt(client, booking, attempt)
        return attempt.status == "REFUNDED"

    payment_status = (
        await _read_payment_status(client, attempt.payment_intent_id)
        if attempt.payment_intent_id
        else None
    )
    if payment_status is None:
        return False  # still unknown; expiry handles a PENDING booking as usual

    now = _now()
    seat_ids = [s.seat_id for s in booking.seats]

    if payment_status not in _PAYMENT_SUCCEEDED:
        attempt.status = "FAILED"
        if booking.status == "PENDING":
            await lock_manager.release_seats(booking.event_id, seat_ids, booking.user_id)
            booking.status = "FAILED"
            booking.updated_at = now
            db.add(
                OutboxEvent(
                    id=uuid.uuid4(),
                    event_type="BOOKING_FAILED",
                    payload={**_booking_payload(booking), "payment_status": payment_status},
                )
            )
        log.info("reconcile_payment_failed", booking_id=str(booking.id))
        return True

    attempt.status = "SUCCEEDED"
    if booking.status not in ("PENDING", "EXPIRED"):
        return True

    try:
        async with db.begin_nested():
            for seat_id in seat_ids:
                db.add(SeatReservation(event_id=booking.event_id, seat_id=seat_id, booking_id=booking.id))
    except IntegrityError:
        # The hold lapsed and the seat was sold to someone else: the customer
        # paid for a seat we can't give them, so refund them in full.
        await lock_manager.release_seats(booking.event_id, seat_ids, booking.user_id)
        booking.status = "FAILED"
        booking.updated_at = now
        attempt.status = "REFUND_PENDING"
        db.add(
            OutboxEvent(
                id=uuid.uuid4(),
                event_type="BOOKING_FAILED",
                payload={**_booking_payload(booking), "reason": "SEAT_UNAVAILABLE_REFUNDED"},
            )
        )
        await _refund_attempt(client, booking, attempt)
        return True

    await lock_manager.release_seats(booking.event_id, seat_ids, booking.user_id)
    booking.status = "CONFIRMED"
    booking.confirmed_at = now
    booking.updated_at = now
    db.add(
        OutboxEvent(
            id=uuid.uuid4(),
            event_type="BOOKING_CONFIRMED",
            payload={**_booking_payload(booking), "reconciled": True},
        )
    )
    log.warning("reconcile_booking_confirmed", booking_id=str(booking.id))
    return True


async def reconcile_unknown_payments(session_factory, lock_manager: LockManager) -> int:
    """Resolve PaymentAttempts left UNKNOWN by confirm_booking (and retry
    pending refunds). Each booking is handled in its own transaction.

    - payment SUCCEEDED -> booking CONFIRMED (even if its hold already
      expired, provided the seats are still free); if the seats were sold
      in the meantime, the charge is refunded in full and the booking FAILED
    - payment FAILED    -> a PENDING booking becomes FAILED, seats released
    - still unknown     -> left alone

    Returns the number of attempts resolved.
    """
    async with session_factory() as db:
        booking_ids = (
            await db.execute(
                select(PaymentAttempt.booking_id).where(PaymentAttempt.status.in_(_RECONCILE_STATUSES))
            )
        ).scalars().all()
    if not booking_ids:
        return 0

    resolved = 0
    async with httpx.AsyncClient(timeout=10.0, headers=internal_headers()) as client:
        for booking_id in booking_ids:
            try:
                async with session_factory() as db:
                    if await _reconcile_one(db, client, lock_manager, booking_id):
                        resolved += 1
                    await db.commit()
            except Exception as exc:
                log.error("payment_reconcile_error", booking_id=str(booking_id), error=str(exc))
    if resolved:
        log.info("payments_reconciled", count=resolved)
    return resolved


async def run_expiry_once(session_factory, lock_manager: LockManager) -> int:
    """One expiry pass, committed on success.

    Unknown payment outcomes are reconciled first, so a booking that was in
    fact paid for is confirmed rather than expired.
    """
    await reconcile_unknown_payments(session_factory, lock_manager)
    async with session_factory() as db:
        expired = await expire_pending_bookings(db, lock_manager)
        await db.commit()
    return expired


# ---------------------------------------------------------------------------
# 8. Startup backfill
# ---------------------------------------------------------------------------


async def backfill_seat_reservations(session_factory) -> int:
    """Reserve the seats of CONFIRMED bookings that predate seat_reservations.

    `create_all` adds the seat_reservations table empty to an existing
    database, which would let every already-sold seat be sold again. Run at
    startup; idempotent. If historical data already sold a seat twice, the
    first booking keeps the reservation and the conflict is logged.

    Returns the number of reservations created.
    """
    async with session_factory() as db:
        already_reserved = (
            select(SeatReservation.id)
            .where(SeatReservation.event_id == Booking.event_id)
            .where(SeatReservation.seat_id == BookingSeat.seat_id)
            .exists()
        )
        rows = (
            await db.execute(
                select(Booking.id, Booking.event_id, BookingSeat.seat_id)
                .join(BookingSeat, BookingSeat.booking_id == Booking.id)
                .where(Booking.status == "CONFIRMED")
                .where(~already_reserved)
                .order_by(Booking.confirmed_at, Booking.id)
            )
        ).all()

        claimed: set[tuple[uuid.UUID, uuid.UUID]] = set()
        for booking_id, event_id, seat_id in rows:
            if (event_id, seat_id) in claimed:
                log.error(
                    "backfill_seat_double_sold",
                    event_id=str(event_id),
                    seat_id=str(seat_id),
                    booking_id=str(booking_id),
                )
                continue
            claimed.add((event_id, seat_id))
            db.add(SeatReservation(event_id=event_id, seat_id=seat_id, booking_id=booking_id))

        try:
            await db.commit()
        except IntegrityError:
            # Another replica backfilled concurrently; its rows win.
            await db.rollback()
            log.warning("backfill_seat_reservations_conflict")
            return 0

    if claimed:
        log.info("seat_reservations_backfilled", count=len(claimed))
    return len(claimed)
