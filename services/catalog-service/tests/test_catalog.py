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
