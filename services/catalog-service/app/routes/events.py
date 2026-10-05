from __future__ import annotations

import math
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.database import get_db
from app.models import Event, EventSeatPrice, Seat, Section, Venue
from libs.security import internal_headers
from app.schemas import (
    EventDetailResponse,
    EventResponse,
    PaginatedEvents,
    SeatMapResponse,
    SeatResponse,
    SeatSectionGroup,
    SeatStatus,
    VenueResponse,
)

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/events", tags=["events"])

settings = get_settings()


# ---------------------------------------------------------------------------
# Helper – fetch locked / sold seat IDs from booking service
# ---------------------------------------------------------------------------


async def _get_unavailable_seat_ids(event_id: uuid.UUID) -> tuple[set[str], set[str]]:
    """
    Call the booking service to retrieve (locked, booked) seat IDs for an event.
    Returns two empty sets on any connection or HTTP error (treat all seats as
    available — booking-service itself still refuses to sell a taken seat).
    """
    url = f"{settings.booking_service_url}/internal/events/{event_id}/locked-seats"
    try:
        async with httpx.AsyncClient(timeout=2.0, headers=internal_headers()) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                return set(data.get("locked_seat_ids", [])), set(data.get("booked_seat_ids", []))
    except Exception as exc:
        logger.warning(
            "booking_service_unreachable",
            event_id=str(event_id),
            error=str(exc),
        )
    return set(), set()


def _seat_status(seat_id: str, locked: set[str], booked: set[str]) -> SeatStatus:
    if seat_id in booked:
        return SeatStatus.booked
    if seat_id in locked:
        return SeatStatus.locked
    return SeatStatus.available


# ---------------------------------------------------------------------------
# GET /events
# ---------------------------------------------------------------------------


@router.get("/", response_model=PaginatedEvents)
async def list_events(
    page: Annotated[int, Query(ge=1)] = 1,
    size: Annotated[int, Query(ge=1, le=100)] = 20,
    city: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
    status: str | None = Query(default="PUBLISHED"),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> PaginatedEvents:
    """List events with optional filters, paginated."""
    query = select(Event)

    if status:
        query = query.where(Event.status == status)
    if city:
        # Join to venue and filter by city
        query = query.join(Venue, Event.venue_id == Venue.id).where(
            func.lower(Venue.city) == func.lower(city)
        )
    if event_type:
        query = query.where(Event.event_type == event_type)
    if date_from:
        query = query.where(Event.event_date >= date_from)
    if date_to:
        query = query.where(Event.event_date <= date_to)

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total: int = total_result.scalar_one()

    # Paginate
    offset = (page - 1) * size
    query = query.order_by(Event.event_date.asc()).offset(offset).limit(size)
    result = await db.execute(query)
    events = result.scalars().all()

    pages = math.ceil(total / size) if total > 0 else 1

    return PaginatedEvents(
        items=[EventResponse.model_validate(e) for e in events],
        total=total,
        page=page,
        size=size,
        pages=pages,
    )


# ---------------------------------------------------------------------------
# GET /events/{event_id}
# ---------------------------------------------------------------------------


@router.get("/{event_id}", response_model=EventDetailResponse)
async def get_event(
    event_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
) -> EventDetailResponse:
    """Return a single event with its venue detail."""
    result = await db.execute(
        select(Event)
        .options(selectinload(Event.venue))
        .where(Event.id == event_id)
    )
    event = result.scalar_one_or_none()
    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "EVENT_NOT_FOUND",
                    "message": f"Event {event_id} not found",
                    "details": {},
                }
            },
        )

    venue_resp = VenueResponse.model_validate(event.venue) if event.venue else None
    return EventDetailResponse(
        id=event.id,
        venue_id=event.venue_id,
        title=event.title,
        description=event.description,
        event_date=event.event_date,
        doors_open=event.doors_open,
        event_type=event.event_type,
        status=event.status,
        base_price=event.base_price,
        currency=event.currency,
        created_at=event.created_at,
        venue=venue_resp,
    )


# ---------------------------------------------------------------------------
# GET /events/{event_id}/seats
# ---------------------------------------------------------------------------


@router.get("/{event_id}/seats", response_model=SeatMapResponse)
async def get_seat_map(
    event_id: uuid.UUID,
    db: AsyncSession = Depends(get_db, scope="function"),
) -> SeatMapResponse:
    """
    Return all seats for an event grouped by section, with per-seat prices
    and live status from the booking service.
    """
    # Verify event exists
    event_result = await db.execute(select(Event).where(Event.id == event_id))
    event = event_result.scalar_one_or_none()
    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "EVENT_NOT_FOUND",
                    "message": f"Event {event_id} not found",
                    "details": {},
                }
            },
        )

    # Load sections for this event's venue (with seats)
    sections_result = await db.execute(
        select(Section)
        .options(selectinload(Section.seats))
        .where(Section.venue_id == event.venue_id)
        .order_by(Section.name)
    )
    sections = sections_result.scalars().all()

    # Load all EventSeatPrices for this event into a lookup dict
    prices_result = await db.execute(
        select(EventSeatPrice).where(EventSeatPrice.event_id == event_id)
    )
    price_map: dict[str, Decimal] = {
        str(esp.seat_id): Decimal(str(esp.price))
        for esp in prices_result.scalars().all()
    }

    # Fetch locked/sold seat IDs from booking service (may return empty sets)
    locked_ids, booked_ids = await _get_unavailable_seat_ids(event_id)

    # Build seat map
    section_groups: list[SeatSectionGroup] = []
    for section in sections:
        seat_responses: list[SeatResponse] = []
        for seat in section.seats:
            seat_id_str = str(seat.id)
            price = price_map.get(seat_id_str, Decimal(str(event.base_price or "0.00")))

            seat_responses.append(
                SeatResponse(
                    id=seat.id,
                    seat_number=seat.seat_number,
                    row_number=seat.row_number,
                    is_accessible=seat.is_accessible,
                    section_id=section.id,
                    section_name=section.name,
                    price=price,
                    status=_seat_status(seat_id_str, locked_ids, booked_ids),
                )
            )

        section_groups.append(
            SeatSectionGroup(
                section_id=section.id,
                section_name=section.name,
                capacity=section.capacity,
                price_multiplier=Decimal(str(section.price_multiplier)),
                seats=seat_responses,
            )
        )

    return SeatMapResponse(event_id=event_id, sections=section_groups)


# ---------------------------------------------------------------------------
# GET /events/{event_id}/seats/{seat_id}
# ---------------------------------------------------------------------------


@router.get("/{event_id}/seats/{seat_id}", response_model=SeatResponse)
async def get_seat(
    event_id: uuid.UUID,
    seat_id: uuid.UUID,
    include_status: bool = Query(
        default=True,
        description="Look up live locked/booked status from booking-service. "
        "Booking-service passes false when it only needs the price.",
    ),
    db: AsyncSession = Depends(get_db, scope="function"),
) -> SeatResponse:
    """Return the status and price for a single seat at an event."""
    # Verify event exists
    event_result = await db.execute(select(Event).where(Event.id == event_id))
    event = event_result.scalar_one_or_none()
    if not event:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "EVENT_NOT_FOUND",
                    "message": f"Event {event_id} not found",
                    "details": {},
                }
            },
        )

    # Load seat with its section
    seat_result = await db.execute(
        select(Seat)
        .options(selectinload(Seat.section))
        .where(Seat.id == seat_id)
    )
    seat = seat_result.scalar_one_or_none()
    # A seat from a different venue is not part of this event.
    if not seat or seat.section.venue_id != event.venue_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "SEAT_NOT_FOUND",
                    "message": f"Seat {seat_id} not found",
                    "details": {},
                }
            },
        )

    # Get price
    price_result = await db.execute(
        select(EventSeatPrice).where(
            EventSeatPrice.event_id == event_id,
            EventSeatPrice.seat_id == seat_id,
        )
    )
    esp = price_result.scalar_one_or_none()
    price = (
        Decimal(str(esp.price))
        if esp
        else Decimal(str(event.base_price or "0.00"))
    )

    # Get status from booking service
    seat_status = SeatStatus.available
    if include_status:
        locked_ids, booked_ids = await _get_unavailable_seat_ids(event_id)
        seat_status = _seat_status(str(seat_id), locked_ids, booked_ids)

    return SeatResponse(
        id=seat.id,
        seat_number=seat.seat_number,
        row_number=seat.row_number,
        is_accessible=seat.is_accessible,
        section_id=seat.section_id,
        section_name=seat.section.name,
        price=price,
        status=seat_status,
    )
