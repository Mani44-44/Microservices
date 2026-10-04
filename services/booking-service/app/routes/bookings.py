"""
routes/bookings.py
~~~~~~~~~~~~~~~~~~
Core booking endpoints: lock seats, confirm, release, fetch single booking.
"""
import uuid

import structlog
from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

import app.booking_service as svc
from app.auth import get_current_user_id
from app.database import get_db
from app.lock_manager import LockManager
from app.schemas import BookingResponse, ConfirmBookingRequest, LockSeatsRequest

log = structlog.get_logger()

router = APIRouter(prefix="/bookings", tags=["bookings"])


def _get_lock_manager(request: Request) -> LockManager:
    """Extract LockManager from app state (redis client set in lifespan)."""
    from app.lock_manager import get_lock_manager

    return get_lock_manager(request.app.state.redis)


@router.post(
    "/lock",
    response_model=BookingResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Lock seats and create a pending booking",
)
async def lock_seats_endpoint(
    body: LockSeatsRequest,
    request: Request,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
    lock_manager: LockManager = Depends(_get_lock_manager),
):
    log.info(
        "lock_seats_request",
        user_id=str(user_id),
        event_id=str(body.event_id),
        seat_count=len(body.seat_ids),
        correlation_id=request.state.correlation_id,
    )
    booking = await svc.lock_seats(body, user_id, db, lock_manager)
    return BookingResponse.model_validate(booking)


@router.post(
    "/{booking_id}/confirm",
    response_model=BookingResponse,
    summary="Confirm (pay for) a pending booking",
)
async def confirm_booking_endpoint(
    booking_id: uuid.UUID,
    body: ConfirmBookingRequest,
    request: Request,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
    lock_manager: LockManager = Depends(_get_lock_manager),
):
    log.info(
        "confirm_booking_request",
        booking_id=str(booking_id),
        user_id=str(user_id),
        correlation_id=request.state.correlation_id,
    )
    booking = await svc.confirm_booking(booking_id, user_id, body, db, lock_manager)
    return BookingResponse.model_validate(booking)


@router.post(
    "/{booking_id}/release",
    response_model=BookingResponse,
    summary="Cancel a pending booking and release seat locks",
)
async def release_booking_endpoint(
    booking_id: uuid.UUID,
    request: Request,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
    lock_manager: LockManager = Depends(_get_lock_manager),
):
    log.info(
        "release_booking_request",
        booking_id=str(booking_id),
        user_id=str(user_id),
        correlation_id=request.state.correlation_id,
    )
    booking = await svc.release_booking(booking_id, user_id, db, lock_manager)
    return BookingResponse.model_validate(booking)


@router.get(
    "/{booking_id}",
    response_model=BookingResponse,
    summary="Get a booking by ID",
)
async def get_booking_endpoint(
    booking_id: uuid.UUID,
    request: Request,
    user_id: uuid.UUID = Depends(get_current_user_id),
    db: AsyncSession = Depends(get_db),
):
    booking = await svc.get_booking(booking_id, user_id, db)
    return BookingResponse.model_validate(booking)
