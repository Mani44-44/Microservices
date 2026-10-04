from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict


# ---------------------------------------------------------------------------
# Error schemas
# ---------------------------------------------------------------------------


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict[str, Any] = {}
    correlation_id: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail


# ---------------------------------------------------------------------------
# Venue schemas
# ---------------------------------------------------------------------------


class SectionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    capacity: int | None
    price_multiplier: Decimal


class VenueResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    address: str | None
    city: str
    country: str
    capacity: int


class VenueDetailResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    address: str | None
    city: str
    country: str
    capacity: int
    sections: list[SectionResponse] = []


# ---------------------------------------------------------------------------
# Event schemas
# ---------------------------------------------------------------------------


class EventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    venue_id: uuid.UUID | None
    title: str
    description: str | None
    event_date: datetime
    doors_open: datetime | None
    event_type: str | None
    status: str
    base_price: Decimal | None
    currency: str
    created_at: datetime | None


class EventDetailResponse(EventResponse):
    venue: VenueResponse | None = None


# ---------------------------------------------------------------------------
# Seat / seat-map schemas
# ---------------------------------------------------------------------------


class SeatStatus(str, Enum):
    available = "available"
    locked = "locked"
    booked = "booked"


class SeatResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    seat_number: str
    row_number: str | None
    is_accessible: bool
    section_id: uuid.UUID
    section_name: str
    price: Decimal
    status: SeatStatus


class SeatSectionGroup(BaseModel):
    section_id: uuid.UUID
    section_name: str
    capacity: int | None
    price_multiplier: Decimal
    seats: list[SeatResponse]


class SeatMapResponse(BaseModel):
    event_id: uuid.UUID
    sections: list[SeatSectionGroup]


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


class PaginatedEvents(BaseModel):
    items: list[EventResponse]
    total: int
    page: int
    size: int
    pages: int
