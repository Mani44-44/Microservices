"""
End-to-end gateway tests.

Each test exercises the gateway in isolation using fakeredis and respx-mocked
downstream services.  No real network calls or Redis server required.

Tests
-----
- test_health_endpoint                — top-level health probe
- test_auth_health_endpoint           — auth-router health probe
- test_register_proxied               — POST /auth/register proxied correctly
- test_protected_endpoint_no_token    — events without token → 401
- test_protected_endpoint_valid_token — events with valid JWT → proxied
- test_protected_endpoint_expired_token — expired JWT → 401
- test_ip_rate_limit                  — IP limit on public auth routes → 429
- test_user_rate_limit                — user limit on protected routes → 429
- test_correlation_id_propagated      — X-Correlation-ID header on responses
- test_cors_headers                   — CORS headers present on all responses
"""

import json
import uuid

import httpx
import pytest
import pytest_asyncio
import respx
from respx.transports import MockTransport
from httpx import AsyncClient, ASGITransport, Response as HttpxResponse

from app.main import app
from app.middleware.rate_limit import RateLimiter
from tests.conftest import (
    TEST_USER_ID,
    _test_settings,
    make_expired_token,
    make_valid_token,
)

import fakeredis.aioredis as fake_aioredis


# ---------------------------------------------------------------------------
# Helper: build a fresh test client with a clean fakeredis instance
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def client_with_mocks():
    """
    Yields (AsyncClient, respx.MockRouter) so individual tests can register
    their own mock routes against downstream services.
    """
    s = _test_settings()
    fr = fake_aioredis.FakeRedis(decode_responses=False)

    with respx.mock(assert_all_called=False) as mock_router:
        mocked_http = httpx.AsyncClient(
            transport=MockTransport(router=mock_router),
            timeout=5.0,
        )

        app.state.redis = fr
        app.state.http_client = mocked_http
        app.state.rate_limiter = RateLimiter(
            redis=fr,
            per_user_limit=s.rate_limit_per_user_per_minute,
            per_ip_limit=s.rate_limit_per_ip_per_minute,
        )

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as ac:
            yield ac, mock_router

    await fr.aclose()
    await mocked_http.aclose()


# ===========================================================================
# Health endpoints
# ===========================================================================

@pytest.mark.asyncio
async def test_health_endpoint(client_with_mocks):
    ac, _ = client_with_mocks
    resp = await ac.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"
    assert body["service"] == "api-gateway"
    assert body["version"] == "1.0.0"


@pytest.mark.asyncio
async def test_auth_health_endpoint(client_with_mocks):
    ac, _ = client_with_mocks
    resp = await ac.get("/api/v1/auth/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"
    assert body["service"] == "api-gateway"


# ===========================================================================
# Auth proxy — public routes
# ===========================================================================

@pytest.mark.asyncio
async def test_register_proxied(client_with_mocks):
    """POST /api/v1/auth/register should be proxied to User Service /register."""
    ac, mock_router = client_with_mocks

    s = _test_settings()
    mock_router.post(f"{s.user_service_url}/register").mock(
        return_value=HttpxResponse(
            201,
            json={"id": str(uuid.uuid4()), "email": "alice@example.com"},
        )
    )

    resp = await ac.post(
        "/api/v1/auth/register",
        json={"email": "alice@example.com", "password": "Secret123!"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert "id" in body


@pytest.mark.asyncio
async def test_login_proxied(client_with_mocks):
    """POST /api/v1/auth/login should be proxied to User Service /login."""
    ac, mock_router = client_with_mocks

    s = _test_settings()
    mock_router.post(f"{s.user_service_url}/login").mock(
        return_value=HttpxResponse(
            200,
            json={"access_token": "tok", "token_type": "bearer"},
        )
    )

    resp = await ac.post(
        "/api/v1/auth/login",
        json={"email": "alice@example.com", "password": "Secret123!"},
    )
    assert resp.status_code == 200
    assert "access_token" in resp.json()


# ===========================================================================
# Protected routes — JWT validation
# ===========================================================================

@pytest.mark.asyncio
async def test_protected_endpoint_no_token(client_with_mocks):
    """GET /api/v1/events without Authorization header must return 401."""
    ac, _ = client_with_mocks
    resp = await ac.get("/api/v1/events/")
    assert resp.status_code == 401
    body = resp.json()
    assert body["error"]["code"] == "MISSING_TOKEN"


@pytest.mark.asyncio
async def test_protected_endpoint_invalid_token(client_with_mocks):
    """A garbage token must return 401 INVALID_TOKEN."""
    ac, _ = client_with_mocks
    resp = await ac.get(
        "/api/v1/events/",
        headers={"Authorization": "Bearer this.is.garbage"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "INVALID_TOKEN"


@pytest.mark.asyncio
async def test_protected_endpoint_expired_token(client_with_mocks):
    """An expired JWT must return 401 TOKEN_EXPIRED."""
    ac, _ = client_with_mocks
    token = make_expired_token()
    resp = await ac.get(
        "/api/v1/events/",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "TOKEN_EXPIRED"


@pytest.mark.asyncio
async def test_protected_endpoint_valid_token(client_with_mocks):
    """A valid JWT must proxy the request to the Catalog Service."""
    ac, mock_router = client_with_mocks

    s = _test_settings()
    mock_router.get(f"{s.catalog_service_url}/events/").mock(
        return_value=HttpxResponse(
            200,
            json={"items": [], "total": 0},
        )
    )

    token = make_valid_token()
    resp = await ac.get(
        "/api/v1/events/",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "total": 0}


@pytest.mark.asyncio
async def test_events_proxy_uses_trailing_slash(client_with_mocks):
    """
    Regression test: the gateway must request catalog-service's /events/
    (trailing slash), not /events. catalog-service's router is mounted at
    prefix="/events" with the list route at "/", so a bare "/events" 307s to
    catalog-service's internal Docker hostname, which the gateway's httpx
    client (no follow_redirects) can't reach externally.
    """
    ac, mock_router = client_with_mocks
    s = _test_settings()

    # Only mock the trailing-slash URL. If the gateway regresses to a bare
    # "/events" request, respx's AllMockedAssertionError fails this test.
    mock_router.get(f"{s.catalog_service_url}/events/").mock(
        return_value=HttpxResponse(200, json={"items": [], "total": 0})
    )

    token = make_valid_token()
    resp = await ac.get(
        "/api/v1/events/",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_x_user_id_forwarded(client_with_mocks):
    """X-User-ID header must be injected with the token subject before proxying."""
    ac, mock_router = client_with_mocks
    s = _test_settings()

    captured_headers: dict = {}

    def capture(request: httpx.Request) -> HttpxResponse:
        captured_headers.update(dict(request.headers))
        return HttpxResponse(200, json={"items": [], "total": 0})

    mock_router.get(f"{s.catalog_service_url}/events/").mock(side_effect=capture)

    token = make_valid_token(user_id=TEST_USER_ID)
    await ac.get("/api/v1/events/", headers={"Authorization": f"Bearer {token}"})

    assert captured_headers.get("x-user-id") == TEST_USER_ID


# ===========================================================================
# Rate limiting
# ===========================================================================

@pytest.mark.asyncio
async def test_ip_rate_limit(client_with_mocks):
    """
    The IP rate limit is 10 req/min in tests.
    After 10 requests, the 11th should return 429.
    Auth routes only apply IP limits.
    """
    ac, mock_router = client_with_mocks
    s = _test_settings()

    mock_router.post(f"{s.user_service_url}/login").mock(
        return_value=HttpxResponse(200, json={"access_token": "tok"})
    )

    for _ in range(s.rate_limit_per_ip_per_minute):
        r = await ac.post(
            "/api/v1/auth/login",
            json={"email": "a@b.com", "password": "pwd"},
        )
        assert r.status_code == 200

    # This request exceeds the limit
    resp = await ac.post(
        "/api/v1/auth/login",
        json={"email": "a@b.com", "password": "pwd"},
    )
    assert resp.status_code == 429
    body = resp.json()
    assert body["error"]["code"] == "RATE_LIMITED"
    assert body["error"]["details"]["retry_after"] == 60


@pytest.mark.asyncio
async def test_user_rate_limit(client_with_mocks):
    """
    The user rate limit is 5 req/min in tests.
    After 5 requests, the 6th protected request should return 429.
    """
    ac, mock_router = client_with_mocks
    s = _test_settings()

    mock_router.get(f"{s.catalog_service_url}/events/").mock(
        return_value=HttpxResponse(200, json={"items": []})
    )

    token = make_valid_token()

    for _ in range(s.rate_limit_per_user_per_minute):
        r = await ac.get(
            "/api/v1/events/",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200

    resp = await ac.get(
        "/api/v1/events/",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "RATE_LIMITED"


# ===========================================================================
# Correlation ID
# ===========================================================================

@pytest.mark.asyncio
async def test_correlation_id_propagated(client_with_mocks):
    """X-Correlation-ID sent in request must echo back in response headers."""
    ac, _ = client_with_mocks
    cid = str(uuid.uuid4())
    resp = await ac.get("/api/v1/health", headers={"X-Correlation-ID": cid})
    assert resp.status_code == 200
    assert resp.headers.get("x-correlation-id") == cid


@pytest.mark.asyncio
async def test_correlation_id_generated_if_absent(client_with_mocks):
    """If no X-Correlation-ID is supplied, the gateway must generate and return one."""
    ac, _ = client_with_mocks
    resp = await ac.get("/api/v1/health")
    assert resp.status_code == 200
    cid = resp.headers.get("x-correlation-id")
    assert cid is not None
    # Must be a valid UUID
    uuid.UUID(cid)


# ===========================================================================
# CORS
# ===========================================================================

@pytest.mark.asyncio
async def test_cors_headers_present(client_with_mocks):
    """An OPTIONS preflight request must return CORS headers."""
    ac, _ = client_with_mocks
    resp = await ac.options(
        "/api/v1/health",
        headers={
            "Origin": "https://example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    # FastAPI's CORSMiddleware returns 200 for preflight
    assert resp.status_code == 200
    assert "access-control-allow-origin" in resp.headers


@pytest.mark.asyncio
async def test_cors_header_on_regular_request(client_with_mocks):
    """Regular GET requests should also receive the CORS allow-origin header."""
    ac, _ = client_with_mocks
    resp = await ac.get(
        "/api/v1/health",
        headers={"Origin": "https://example.com"},
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "*"


# ===========================================================================
# Booking proxy smoke tests
# ===========================================================================

@pytest.mark.asyncio
async def test_booking_lock_proxied(client_with_mocks):
    """POST /api/v1/bookings/lock should proxy to Booking Service."""
    ac, mock_router = client_with_mocks
    s = _test_settings()

    booking_id = str(uuid.uuid4())
    mock_router.post(f"{s.booking_service_url}/bookings/lock").mock(
        return_value=HttpxResponse(201, json={"booking_id": booking_id})
    )

    token = make_valid_token()
    resp = await ac.post(
        "/api/v1/bookings/lock",
        json={"event_id": str(uuid.uuid4()), "seat_ids": ["A1"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201
    assert resp.json()["booking_id"] == booking_id


@pytest.mark.asyncio
async def test_get_booking_proxied(client_with_mocks):
    """GET /api/v1/bookings/{id} should proxy to Booking Service."""
    ac, mock_router = client_with_mocks
    s = _test_settings()

    booking_id = str(uuid.uuid4())
    mock_router.get(f"{s.booking_service_url}/bookings/{booking_id}").mock(
        return_value=HttpxResponse(200, json={"id": booking_id, "status": "locked"})
    )

    token = make_valid_token()
    resp = await ac.get(
        f"/api/v1/bookings/{booking_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == booking_id
