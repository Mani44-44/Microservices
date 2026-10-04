"""
Pytest configuration and shared fixtures for the API Gateway test suite.

Strategy
--------
- ``fakeredis`` replaces real Redis so rate-limit Lua scripts execute
  in-process without needing a running Redis server.
- ``respx`` (httpx mock router) intercepts all outbound httpx calls so
  we never hit real downstream services.
- The FastAPI ``TestClient`` is replaced by the async ``AsyncClient``
  so we can test async routes properly.
"""

import datetime
import uuid
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import fakeredis.aioredis as fake_aioredis
import httpx
import jwt
import pytest
import pytest_asyncio
import respx
from respx.transports import MockTransport
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from app.config import Settings, get_settings
from app.main import app
from app.middleware.rate_limit import RateLimiter


# ---------------------------------------------------------------------------
# Settings override — use a known secret so tests can mint valid tokens
# ---------------------------------------------------------------------------
TEST_SECRET = "test-super-secret-jwt-key-that-is-at-least-32-chars"
TEST_USER_ID = str(uuid.uuid4())


def _test_settings() -> Settings:
    return Settings(
        redis_url="redis://localhost:6379",   # not actually used; fakeredis patches it
        jwt_secret_key=TEST_SECRET,
        jwt_algorithm="HS256",
        user_service_url="http://user-service",
        catalog_service_url="http://catalog-service",
        booking_service_url="http://booking-service",
        payment_service_url="http://payment-service",
        rate_limit_per_user_per_minute=5,    # low so tests can hit the limit quickly
        rate_limit_per_ip_per_minute=10,
    )


# ---------------------------------------------------------------------------
# Override the cached settings singleton for all tests in this session
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True, scope="session")
def override_settings():
    app.dependency_overrides[get_settings] = _test_settings
    # Also patch the module-level ``settings`` references in route modules
    import app.routes.auth as auth_mod
    import app.routes.events as events_mod
    import app.routes.bookings as bookings_mod
    import app.middleware.auth as auth_mw

    s = _test_settings()
    auth_mod.settings = s
    events_mod.settings = s
    bookings_mod.settings = s
    auth_mw.settings = s
    yield


# ---------------------------------------------------------------------------
# Fake Redis fixture (session-scoped so it persists across tests in a class)
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def fake_redis():
    """In-process Redis replacement that supports Lua scripting."""
    r = fake_aioredis.FakeRedis(decode_responses=False)
    yield r
    await r.aclose()


# ---------------------------------------------------------------------------
# Rate limiter backed by fake Redis
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def rate_limiter(fake_redis):
    s = _test_settings()
    return RateLimiter(
        redis=fake_redis,
        per_user_limit=s.rate_limit_per_user_per_minute,
        per_ip_limit=s.rate_limit_per_ip_per_minute,
    )


# ---------------------------------------------------------------------------
# ASGI test client — wires fake Redis + httpx mock into app.state
# ---------------------------------------------------------------------------
@pytest_asyncio.fixture
async def client(fake_redis) -> AsyncGenerator[AsyncClient, None]:
    """
    Async test client with:
      - fakeredis wired into app.state.redis / app.state.rate_limiter
      - respx mock router intercepting all httpx calls
    """
    s = _test_settings()

    async with respx.MockRouter(assert_all_called=False) as mock_router:
        # Build a real httpx.AsyncClient whose transport is the respx mock
        mocked_http = httpx.AsyncClient(
            transport=MockTransport(router=mock_router),
            timeout=5.0,
        )

        # Patch app.state before the lifespan runs
        app.state.redis = fake_redis
        app.state.http_client = mocked_http
        app.state.rate_limiter = RateLimiter(
            redis=fake_redis,
            per_user_limit=s.rate_limit_per_user_per_minute,
            per_ip_limit=s.rate_limit_per_ip_per_minute,
        )

        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://testserver",
        ) as ac:
            yield ac

        await mocked_http.aclose()


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------
def make_valid_token(user_id: str = TEST_USER_ID, secret: str = TEST_SECRET) -> str:
    payload = {
        "sub": user_id,
        "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=1),
        "iat": datetime.datetime.utcnow(),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def make_expired_token(user_id: str = TEST_USER_ID, secret: str = TEST_SECRET) -> str:
    payload = {
        "sub": user_id,
        "exp": datetime.datetime.utcnow() - datetime.timedelta(hours=1),
        "iat": datetime.datetime.utcnow() - datetime.timedelta(hours=2),
    }
    return jwt.encode(payload, secret, algorithm="HS256")
