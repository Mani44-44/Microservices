"""
Core tests for the Booking Service: lock -> confirm -> release flows,
ownership checks, and the seat-conflict / expiry edge cases.
"""
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import auth_headers, make_token

pytestmark = pytest.mark.asyncio


def _lock_body(**overrides) -> dict:
    defaults = {
        "event_id": str(uuid.uuid4()),
        "seat_ids": [str(uuid.uuid4())],
    }
    defaults.update(overrides)
    return defaults


def _mock_payment_client(confirm_status: str = "SUCCEEDED"):
    """Patch app.booking_service.httpx.AsyncClient to fake the payment-service round trip."""
    create_resp = MagicMock(status_code=201)
    create_resp.json.return_value = {"id": str(uuid.uuid4())}

    confirm_resp = MagicMock(status_code=200)
    confirm_resp.json.return_value = {"status": confirm_status}

    mock_client = AsyncMock()
    mock_client.post.side_effect = [create_resp, confirm_resp]
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = False
    return mock_client


async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200


async def test_lock_seats_success(client):
    user_id = uuid.uuid4()
    resp = await client.post(
        "/bookings/lock", json=_lock_body(), headers=auth_headers(user_id)
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "PENDING"
    assert body["user_id"] == str(user_id)
    assert len(body["seats"]) == 1


async def test_lock_seats_no_token(client):
    resp = await client.post("/bookings/lock", json=_lock_body())
    assert resp.status_code == 401  # HTTPBearer auto_error rejects missing header


async def test_lock_seats_too_many_seats(client):
    # Schema caps seat_ids at max_length=10, so this is rejected by Pydantic
    # validation before app.booking_service's own TOO_MANY_SEATS check runs.
    body = _lock_body(seat_ids=[str(uuid.uuid4()) for _ in range(11)])
    resp = await client.post("/bookings/lock", json=body, headers=auth_headers())
    assert resp.status_code == 422


async def test_lock_seats_conflict_on_same_seat(client):
    event_id = str(uuid.uuid4())
    seat_id = str(uuid.uuid4())
    body = _lock_body(event_id=event_id, seat_ids=[seat_id])

    r1 = await client.post("/bookings/lock", json=body, headers=auth_headers())
    assert r1.status_code == 201

    r2 = await client.post("/bookings/lock", json=body, headers=auth_headers())
    assert r2.status_code == 409
    assert r2.json()["error"]["code"] == "SEAT_ALREADY_LOCKED"


async def test_confirm_booking_success(client):
    user_id = uuid.uuid4()
    lock_resp = await client.post(
        "/bookings/lock", json=_lock_body(), headers=auth_headers(user_id)
    )
    booking_id = lock_resp.json()["id"]

    with patch("app.booking_service.httpx.AsyncClient", return_value=_mock_payment_client()):
        resp = await client.post(
            f"/bookings/{booking_id}/confirm",
            json={"payment_method": "tok_visa"},
            headers=auth_headers(user_id),
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "CONFIRMED"


async def test_confirm_booking_wrong_user(client):
    owner_id = uuid.uuid4()
    lock_resp = await client.post(
        "/bookings/lock", json=_lock_body(), headers=auth_headers(owner_id)
    )
    booking_id = lock_resp.json()["id"]

    resp = await client.post(
        f"/bookings/{booking_id}/confirm",
        json={"payment_method": "tok_visa"},
        headers=auth_headers(uuid.uuid4()),
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


async def test_confirm_booking_payment_failure_releases_seats(client):
    event_id = str(uuid.uuid4())
    seat_id = str(uuid.uuid4())
    user_id = uuid.uuid4()

    lock_resp = await client.post(
        "/bookings/lock",
        json=_lock_body(event_id=event_id, seat_ids=[seat_id]),
        headers=auth_headers(user_id),
    )
    booking_id = lock_resp.json()["id"]

    with patch(
        "app.booking_service.httpx.AsyncClient",
        return_value=_mock_payment_client(confirm_status="FAILED"),
    ):
        resp = await client.post(
            f"/bookings/{booking_id}/confirm",
            json={"payment_method": "tok_visa"},
            headers=auth_headers(user_id),
        )
    assert resp.status_code == 200
    assert resp.json()["status"] == "FAILED"

    # Seats released -> a fresh lock attempt on the same seat now succeeds
    retry = await client.post(
        "/bookings/lock",
        json=_lock_body(event_id=event_id, seat_ids=[seat_id]),
        headers=auth_headers(uuid.uuid4()),
    )
    assert retry.status_code == 201


async def test_confirm_booking_lock_renewal_failure_expires_booking(client, monkeypatch):
    from app.lock_manager import LockManager

    user_id = uuid.uuid4()
    lock_resp = await client.post(
        "/bookings/lock", json=_lock_body(), headers=auth_headers(user_id)
    )
    booking_id = lock_resp.json()["id"]

    monkeypatch.setattr(
        LockManager, "extend_lock_ttl", AsyncMock(return_value=False)
    )

    with patch("app.booking_service.httpx.AsyncClient") as mock_client_cls:
        resp = await client.post(
            f"/bookings/{booking_id}/confirm",
            json={"payment_method": "tok_visa"},
            headers=auth_headers(user_id),
        )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "BOOKING_EXPIRED"
    mock_client_cls.assert_not_called()


async def test_release_booking(client):
    user_id = uuid.uuid4()
    lock_resp = await client.post(
        "/bookings/lock", json=_lock_body(), headers=auth_headers(user_id)
    )
    booking_id = lock_resp.json()["id"]

    resp = await client.post(
        f"/bookings/{booking_id}/release", headers=auth_headers(user_id)
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "CANCELLED"


async def test_get_booking_not_found(client):
    resp = await client.get(f"/bookings/{uuid.uuid4()}", headers=auth_headers())
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "BOOKING_NOT_FOUND"


async def test_user_bookings_forbidden_for_other_user(client):
    resp = await client.get(
        f"/users/{uuid.uuid4()}/bookings", headers=auth_headers()
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "FORBIDDEN"


async def test_user_bookings_own(client):
    user_id = uuid.uuid4()
    await client.post("/bookings/lock", json=_lock_body(), headers=auth_headers(user_id))

    resp = await client.get(
        f"/users/{user_id}/bookings", headers=auth_headers(user_id)
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1


async def test_internal_locked_seats(client):
    event_id = str(uuid.uuid4())
    seat_id = str(uuid.uuid4())
    await client.post(
        "/bookings/lock",
        json=_lock_body(event_id=event_id, seat_ids=[seat_id]),
        headers=auth_headers(),
    )

    resp = await client.get(f"/internal/events/{event_id}/locked-seats")
    assert resp.status_code == 200
    body = resp.json()
    assert seat_id in body["locked_seat_ids"]
    assert body["count"] == 1
