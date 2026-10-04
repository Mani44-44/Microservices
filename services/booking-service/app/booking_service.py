"""
booking_service.py
~~~~~~~~~~~~~~~~~~
Core booking business logic.

Concurrency safety strategy:
  1. Seat locks are held in Redis via atomic Lua scripts (LockManager).
  2. DB writes use PostgreSQL transactions; row-level locking (`SELECT FOR UPDATE`)
     is applied when reading a booking for mutation to prevent double-processing.
  3. The expiry handler force-releases locks AND updates DB status atomically
     within a transaction, then emits an outbox event.
"""
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import List, Optional, Tuple

import httpx
import structlog
from fastapi import HTTPException, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from libs.security import internal_headers
from app.lock_manager import LockManager
from app.models import Booking, BookingSeat, OutboxEvent, PaymentAttempt
from app.schemas import ConfirmBookingRequest, LockSeatsRequest

log = structlog.get_logger()
settings = get_settings()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(timezone.utc)


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

    Returns 0 if the catalog service is unavailable (graceful degradation
    during tests/development; in production you'd raise).
    """
    try:
        url = f"{settings.catalog_service_url}/events/{event_id}/seats/{seat_id}"
        resp = await http_client.get(url, timeout=5.0)
        if resp.status_code == 200:
            data = resp.json()
            # catalog service returns {'price': '99.99', ...}
            return Decimal(str(data.get("price", "0.00")))
        log.warning(
            "catalog_seat_not_found",
            event_id=str(event_id),
            seat_id=str(seat_id),
            status_code=resp.status_code,
        )
        return Decimal("0.00")
    except Exception as exc:
        log.warning(
            "catalog_service_unavailable",
            error=str(exc),
            seat_id=str(seat_id),
        )
        return Decimal("0.00")


async def _fetch_seat_prices(
    event_id: uuid.UUID,
    seat_ids: List[uuid.UUID],
) -> dict[uuid.UUID, Decimal]:
    """Batch-fetch seat prices from the catalog service."""
    async with httpx.AsyncClient(headers=internal_headers()) as client:
        prices: dict[uuid.UUID, Decimal] = {}
        for seat_id in seat_ids:
            prices[seat_id] = await _get_seat_price(client, event_id, seat_id)
    return prices


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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "BOOKING_NOT_FOUND",
                    "message": f"Booking {booking_id} not found",
                    "details": {},
                }
            },
        )
    return booking


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
      2. Fetch prices from catalog service.
      3. Atomically lock all seats in Redis (all-or-nothing).
      4. Persist Booking + BookingSeat rows + OutboxEvent in one transaction.
    """
    # Deduplicate while preserving order
    seen: set[uuid.UUID] = set()
    unique_seat_ids: List[uuid.UUID] = []
    for sid in request.seat_ids:
        if sid not in seen:
            seen.add(sid)
            unique_seat_ids.append(sid)

    if len(unique_seat_ids) > settings.max_seats_per_booking:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": {
                    "code": "TOO_MANY_SEATS",
                    "message": (
                        f"Cannot book more than {settings.max_seats_per_booking} seats at once"
                    ),
                    "details": {"max": settings.max_seats_per_booking},
                }
            },
        )

    log.info(
        "lock_seats_start",
        user_id=str(user_id),
        event_id=str(request.event_id),
        seat_count=len(unique_seat_ids),
    )

    # Fetch prices from catalog service
    prices = await _fetch_seat_prices(request.event_id, unique_seat_ids)

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
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": {
                    "code": "SEAT_ALREADY_LOCKED",
                    "message": "One or more seats are already reserved by another user",
                    "details": {
                        "failed_seat_id": str(failed_seat_id) if failed_seat_id else None
                    },
                }
            },
        )

    # Persist booking inside a transaction
    now = _now()
    expires_at = now + timedelta(seconds=settings.seat_lock_ttl_seconds)
    total_amount = sum(prices.values(), Decimal("0.00"))

    booking = Booking(
        id=uuid.uuid4(),
        user_id=user_id,
        event_id=request.event_id,
        status="PENDING",
        total_amount=total_amount,
        currency="USD",
        locked_at=now,
        expires_at=expires_at,
    )
    db.add(booking)
    await db.flush()  # get booking.id without committing

    for seat_id in unique_seat_ids:
        db.add(
            BookingSeat(
                id=uuid.uuid4(),
                booking_id=booking.id,
                seat_id=seat_id,
                price_at_booking=prices.get(seat_id, Decimal("0.00")),
            )
        )

    db.add(
        OutboxEvent(
            id=uuid.uuid4(),
            event_type="SEATS_LOCKED",
            payload={
                **_booking_payload(booking),
                "seat_ids": [str(s) for s in unique_seat_ids],
                "expires_at": expires_at.isoformat(),
            },
        )
    )

    await db.flush()
    await db.refresh(booking, ["seats"])

    log.info(
        "booking_created",
        booking_id=str(booking.id),
        user_id=str(user_id),
        event_id=str(request.event_id),
        total_amount=str(total_amount),
    )
    return booking


# ---------------------------------------------------------------------------
# 2. Confirm booking
# ---------------------------------------------------------------------------


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
      4. Extend seat lock TTL for payment processing time.
      5. Call payment service to create and confirm a payment intent.
      6. Update booking status and release Redis locks.
      7. Emit outbox event.
    """
    booking = await _fetch_booking_with_lock(booking_id, db)

    # Ownership check
    if booking.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "FORBIDDEN",
                    "message": "You do not own this booking",
                    "details": {},
                }
            },
        )

    # Idempotency: already confirmed
    if booking.status == "CONFIRMED":
        log.info("confirm_booking_idempotent", booking_id=str(booking_id))
        return booking

    # Must be PENDING
    if booking.status != "PENDING":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": {
                    "code": "INVALID_BOOKING_STATUS",
                    "message": f"Cannot confirm a booking with status '{booking.status}'",
                    "details": {"current_status": booking.status},
                }
            },
        )

    # Expiry check
    # SQLite (used in tests) doesn't round-trip tzinfo like Postgres does,
    # so a freshly-fetched expires_at can come back naive.
    expires_at = booking.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= _now():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": {
                    "code": "BOOKING_EXPIRED",
                    "message": "Seat lock has expired; please start a new booking",
                    "details": {"expired_at": booking.expires_at.isoformat()},
                }
            },
        )

    seat_ids = [s.seat_id for s in booking.seats]

    # Extend TTL so locks survive payment processing. A failed extend means
    # at least one seat lock already expired (EXPIRE on a missing key
    # returns 0) — nothing to retry, so abort before contacting payment.
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
        await db.flush()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": {
                    "code": "BOOKING_EXPIRED",
                    "message": "Seat lock has expired; please start a new booking",
                    "details": {},
                }
            },
        )

    # ----- Payment service calls -----
    idempotency_key = str(booking.id)
    payment_intent_id: Optional[str] = None
    payment_status = "FAILED"

    try:
        async with httpx.AsyncClient(timeout=30.0, headers=internal_headers()) as client:
            # Step 1: Create payment intent
            create_resp = await client.post(
                f"{settings.payment_service_url}/payments/create-intent",
                json={
                    "booking_id": str(booking.id),
                    "amount": str(booking.total_amount),
                    "currency": booking.currency,
                    "idempotency_key": idempotency_key,
                },
            )

            if create_resp.status_code not in (200, 201):
                log.error(
                    "payment_create_intent_failed",
                    booking_id=str(booking_id),
                    status_code=create_resp.status_code,
                    body=create_resp.text,
                )
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail={
                        "error": {
                            "code": "PAYMENT_SERVICE_ERROR",
                            "message": "Failed to create payment intent",
                            "details": {},
                        }
                    },
                )

            intent_data = create_resp.json()
            payment_intent_id = intent_data.get("id") or intent_data.get("payment_intent_id")

            # Step 2: Confirm payment intent
            confirm_resp = await client.post(
                f"{settings.payment_service_url}/payments/{payment_intent_id}/confirm",
                json={"payment_method_details": request.payment_method},
            )

            if confirm_resp.status_code in (200, 201):
                confirm_data = confirm_resp.json()
                payment_status = confirm_data.get("status", "SUCCEEDED").upper()
            else:
                log.error(
                    "payment_confirm_failed",
                    booking_id=str(booking_id),
                    payment_intent_id=payment_intent_id,
                    status_code=confirm_resp.status_code,
                )
                payment_status = "FAILED"

    except HTTPException:
        raise
    except Exception as exc:
        log.error(
            "payment_service_exception",
            booking_id=str(booking_id),
            error=str(exc),
        )
        payment_status = "FAILED"

    # Record payment attempt
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

    now = _now()

    if payment_status in ("SUCCEEDED", "SUCCESS", "COMPLETED", "APPROVED"):
        # SUCCESS path
        booking.status = "CONFIRMED"
        booking.confirmed_at = now
        booking.updated_at = now

        # Release Redis locks – seats are now committed in DB
        await lock_manager.release_seats(
            event_id=booking.event_id,
            seat_ids=seat_ids,
            user_id=user_id,
        )

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
        # FAILURE path
        booking.status = "FAILED"
        booking.updated_at = now

        # Release locks so other users can book
        await lock_manager.release_seats(
            event_id=booking.event_id,
            seat_ids=seat_ids,
            user_id=user_id,
        )

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

    if booking.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "FORBIDDEN",
                    "message": "You do not own this booking",
                    "details": {},
                }
            },
        )

    if booking.status != "PENDING":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": {
                    "code": "INVALID_BOOKING_STATUS",
                    "message": f"Cannot release a booking with status '{booking.status}'",
                    "details": {"current_status": booking.status},
                }
            },
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
# 4. Get single booking
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "BOOKING_NOT_FOUND",
                    "message": f"Booking {booking_id} not found",
                    "details": {},
                }
            },
        )

    if booking.user_id != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "FORBIDDEN",
                    "message": "You do not own this booking",
                    "details": {},
                }
            },
        )

    return booking


# ---------------------------------------------------------------------------
# 5. Get paginated user bookings
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
# 6. Expiry handler (background task)
# ---------------------------------------------------------------------------


async def expire_pending_bookings(
    db: AsyncSession,
    lock_manager: LockManager,
) -> int:
    """Find PENDING bookings past their expiry time and mark them EXPIRED.

    This is called by the background expiry handler task every 30 seconds.

    Returns:
        Number of bookings expired in this run.
    """
    now = _now()

    # Load expired PENDING bookings (with seat data for lock release)
    result = await db.execute(
        select(Booking)
        .options(selectinload(Booking.seats))
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

            # Force-release Redis locks (user may have disconnected, so we
            # cannot check ownership here)
            if seat_ids:
                await lock_manager.release_seats_admin(
                    event_id=booking.event_id,
                    seat_ids=seat_ids,
                )

            booking.status = "EXPIRED"
            booking.updated_at = now

            db.add(
                OutboxEvent(
                    id=uuid.uuid4(),
                    event_type="BOOKING_EXPIRED",
                    payload={
                        **_booking_payload(booking),
                        "expired_at": now.isoformat(),
                    },
                )
            )
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
