from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class LockSeatsRequest(BaseModel):
    """Request body to lock seats for a booking."""

    event_id: UUID
    seat_ids: List[UUID] = Field(..., min_length=1, max_length=10)


class ConfirmBookingRequest(BaseModel):
    """Request body to confirm (pay for) a pending booking."""

    payment_method: str = Field(..., min_length=1, description="Payment method token / details")


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class BookingSeatResponse(BaseModel):
    """Single seat attached to a booking."""

    seat_id: UUID
    price_at_booking: Decimal

    model_config = {"from_attributes": True}


class BookingResponse(BaseModel):
    """Full booking representation returned to clients."""

    id: UUID
    user_id: UUID
    event_id: UUID
    status: str
    total_amount: Decimal
    currency: str
    locked_at: Optional[datetime] = None
    expires_at: datetime
    confirmed_at: Optional[datetime] = None
    cancelled_at: Optional[datetime] = None
    seats: List[BookingSeatResponse] = []
    created_at: datetime

    model_config = {"from_attributes": True}


class PaginatedBookings(BaseModel):
    """Paginated list of bookings."""

    items: List[BookingResponse]
    total: int
    page: int
    size: int


# ---------------------------------------------------------------------------
# Error schemas
# ---------------------------------------------------------------------------


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict = {}
    correlation_id: Optional[str] = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


# ---------------------------------------------------------------------------
# Internal schemas
# ---------------------------------------------------------------------------


class LockedSeatsResponse(BaseModel):
    """Response for the internal locked-seats endpoint."""

    event_id: UUID
    locked_seat_ids: List[str]
    count: int
