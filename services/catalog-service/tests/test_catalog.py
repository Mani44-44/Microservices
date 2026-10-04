"""
Test suite for the catalog-service.

Uses an in-memory SQLite DB (via conftest fixtures) and httpx.AsyncClient.
The booking-service call in GET /events/{id}/seats is handled gracefully:
when it is unreachable all seats are shown as 'available'.
"""
from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from app.models import Event, Venue

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# /events
# ---------------------------------------------------------------------------


async def test_list_events(client: AsyncClient, published_event: Event):
    """GET /events returns a paginated list that includes the published event."""
    resp = await client.get("/events/")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert data["total"] >= 1
    assert data["page"] == 1
    titles = [e["title"] for e in data["items"]]
    assert published_event.title in titles


async def test_list_events_filter_city(client: AsyncClient, published_event: Event, venue: Venue):
    """GET /events?city=NYC returns events in that city."""
    resp = await client.get("/events/", params={"city": "NYC"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] >= 1
    # All returned events should belong to a venue in NYC (implicitly tested
    # by the fact that our venue is in NYC and the event is listed)
    titles = [e["title"] for e in data["items"]]
    assert published_event.title in titles


async def test_list_events_filter_type(client: AsyncClient, published_event: Event):
    """GET /events?event_type=concert returns only concerts."""
    resp = await client.get("/events/", params={"event_type": "concert", "status": "PUBLISHED"})
    assert resp.status_code == 200
    data = resp.json()
    for item in data["items"]:
        assert item["event_type"] == "concert"


async def test_list_events_pagination(client: AsyncClient, published_event: Event):
    """Pagination parameters are reflected in the response."""
    resp = await client.get("/events/", params={"page": 1, "size": 5})
    assert resp.status_code == 200
    data = resp.json()
    assert data["page"] == 1
    assert data["size"] == 5
    assert len(data["items"]) <= 5


async def test_list_events_draft_not_in_published(
    client: AsyncClient, published_event: Event, draft_event: Event
):
    """Draft events must not appear in the default PUBLISHED listing."""
    resp = await client.get("/events/", params={"status": "PUBLISHED"})
    assert resp.status_code == 200
    data = resp.json()
    titles = [e["title"] for e in data["items"]]
    assert draft_event.title not in titles
    assert published_event.title in titles


# ---------------------------------------------------------------------------
# /events/{event_id}
# ---------------------------------------------------------------------------


async def test_get_event_detail(client: AsyncClient, published_event: Event):
    """GET /events/{id} returns full event with venue info."""
    resp = await client.get(f"/events/{published_event.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == str(published_event.id)
    assert data["title"] == published_event.title
    assert "venue" in data
    # venue should contain city
    if data["venue"] is not None:
        assert data["venue"]["city"] == "NYC"


async def test_get_event_not_found(client: AsyncClient):
    """GET /events/{unknown_id} returns 404."""
    resp = await client.get(f"/events/{uuid.uuid4()}")
    assert resp.status_code == 404
    data = resp.json()
    assert "error" in data or "detail" in data


# ---------------------------------------------------------------------------
# /events/{event_id}/seats
# ---------------------------------------------------------------------------


async def test_get_seat_map(client: AsyncClient, published_event: Event):
    """
    GET /events/{id}/seats returns a SeatMapResponse grouped by section.
    Since booking-service is not running in tests, all seats show as 'available'.
    """
    resp = await client.get(f"/events/{published_event.id}/seats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["event_id"] == str(published_event.id)
    assert "sections" in data
    assert isinstance(data["sections"], list)
    # At least one section should be present (Floor)
    assert len(data["sections"]) >= 1
    # Each section has seats
    for section_group in data["sections"]:
        assert "section_name" in section_group
        assert "seats" in section_group
        for seat in section_group["seats"]:
            assert seat["status"] in ("available", "locked", "booked")
            assert "price" in seat


# ---------------------------------------------------------------------------
# /venues
# ---------------------------------------------------------------------------


async def test_list_venues(client: AsyncClient, venue: Venue):
    """GET /venues returns a list containing the seeded venue."""
    resp = await client.get("/venues/")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    ids = [v["id"] for v in data]
    assert str(venue.id) in ids


async def test_get_venue_detail(client: AsyncClient, venue: Venue):
    """GET /venues/{id} returns the venue with its sections."""
    resp = await client.get(f"/venues/{venue.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == str(venue.id)
    assert data["name"] == "Test Arena"
    assert "sections" in data
    section_names = [s["name"] for s in data["sections"]]
    assert "Floor" in section_names
    assert "Upper Bowl" in section_names


async def test_get_venue_not_found(client: AsyncClient):
    """GET /venues/{unknown_id} returns 404."""
    resp = await client.get(f"/venues/{uuid.uuid4()}")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------


async def test_health(client: AsyncClient):
    """GET /health returns healthy status."""
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in ("healthy", "degraded")
    assert data["service"] == "catalog-service"
    assert "checks" in data
    assert "database" in data["checks"]


# ---------------------------------------------------------------------------
# Seat validity and sold-seat status
# ---------------------------------------------------------------------------


def _booking_service_returning(locked=(), booked=()):
    from unittest.mock import AsyncMock, MagicMock

    resp = MagicMock(status_code=200)
    resp.json.return_value = {
        "locked_seat_ids": [str(s) for s in locked],
        "booked_seat_ids": [str(s) for s in booked],
    }
    client = AsyncMock()
    client.get.return_value = resp
    client.__aenter__.return_value = client
    client.__aexit__.return_value = False
    return client


async def test_get_seat_rejects_seat_from_another_venue(client: AsyncClient, db, published_event: Event):
    from app.models import Seat, Section

    other_venue = Venue(id=uuid.uuid4(), name="Elsewhere", city="LA", country="US", capacity=10)
    db.add(other_venue)
    await db.flush()
    section = Section(id=uuid.uuid4(), venue_id=other_venue.id, name="GA", capacity=10)
    db.add(section)
    await db.flush()
    foreign_seat = Seat(id=uuid.uuid4(), section_id=section.id, seat_number="1", row_number="Z")
    db.add(foreign_seat)
    await db.commit()

    resp = await client.get(f"/events/{published_event.id}/seats/{foreign_seat.id}")

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "SEAT_NOT_FOUND"


async def test_seat_map_reports_booked_and_locked_seats(
    client: AsyncClient, venue: Venue, published_event: Event
):
    from unittest.mock import patch

    locked_seat, booked_seat = venue._floor_seats[0], venue._floor_seats[1]
    booking_service = _booking_service_returning(locked=[locked_seat.id], booked=[booked_seat.id])
    with patch("app.routes.events.httpx.AsyncClient", return_value=booking_service):
        resp = await client.get(f"/events/{published_event.id}/seats")

    statuses = {
        seat["id"]: seat["status"]
        for section in resp.json()["sections"]
        for seat in section["seats"]
    }
    assert statuses[str(locked_seat.id)] == "locked"
    assert statuses[str(booked_seat.id)] == "booked"
    assert statuses[str(venue._floor_seats[2].id)] == "available"


async def test_get_seat_reports_booked(client: AsyncClient, venue: Venue, published_event: Event):
    from unittest.mock import patch

    seat = venue._floor_seats[0]
    with patch(
        "app.routes.events.httpx.AsyncClient",
        return_value=_booking_service_returning(booked=[seat.id]),
    ):
        resp = await client.get(f"/events/{published_event.id}/seats/{seat.id}")

    assert resp.status_code == 200
    assert resp.json()["status"] == "booked"
    assert resp.json()["price"] == "200.00"


async def test_get_seat_can_skip_status_lookup(client: AsyncClient, venue: Venue, published_event: Event):
    """Booking-service only needs the price; skip the call back into it."""
    from unittest.mock import patch

    seat = venue._floor_seats[0]
    with patch("app.routes.events.httpx.AsyncClient") as client_cls:
        resp = await client.get(
            f"/events/{published_event.id}/seats/{seat.id}", params={"include_status": "false"}
        )

    assert resp.status_code == 200
    assert resp.json()["price"] == "200.00"
    client_cls.assert_not_called()
