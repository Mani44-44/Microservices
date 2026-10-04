"""
Test configuration and shared fixtures for the Booking Service.

Uses an in-process SQLite database (no real PostgreSQL) and fakeredis
(no real Redis) so tests run without any external services.
"""
import asyncio
import uuid
from decimal import Decimal
from typing import AsyncGenerator

import jwt
import pytest
import pytest_asyncio
from fakeredis import aioredis as fake_aioredis
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import get_settings
from app.database import Base, get_db
from app.main import app
from libs.security import internal_headers

settings = get_settings()


@pytest.fixture(scope="session")
def event_loop_policy():
    return asyncio.DefaultEventLoopPolicy()


TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture(scope="session")
async def test_engine():
    engine = create_async_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        echo=False,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(scope="session")
async def test_session_factory(test_engine):
    return async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)


@pytest_asyncio.fixture()
async def db_session(test_engine, test_session_factory) -> AsyncGenerator[AsyncSession, None]:
    """Each test gets a fresh session that is rolled back on completion."""
    async with test_engine.connect() as conn:
        await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False)
        try:
            yield session
        finally:
            await session.close()
            await conn.rollback()


@pytest_asyncio.fixture()
async def fake_redis():
    r = fake_aioredis.FakeRedis(decode_responses=True)
    yield r
    await r.aclose()


@pytest_asyncio.fixture()
async def client(db_session: AsyncSession, fake_redis) -> AsyncGenerator[AsyncClient, None]:
    """HTTP client with DB + Redis overridden with fast, in-process fakes."""

    async def _override_get_db():
        try:
            yield db_session
            await db_session.flush()
        except Exception:
            await db_session.rollback()
            raise

    app.dependency_overrides[get_db] = _override_get_db
    app.state.redis = fake_redis

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", headers=internal_headers()
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "real_pricing: do not stub catalog seat pricing for this test"
    )


SEAT_PRICE = Decimal("50.00")


@pytest.fixture(autouse=True)
def stub_seat_prices(request, monkeypatch):
    """Price every seat at SEAT_PRICE instead of calling catalog-service.

    Seat pricing fails closed (no catalog -> no booking), so tests must not
    depend on a live catalog. Opt out with @pytest.mark.real_pricing.
    """
    if request.node.get_closest_marker("real_pricing"):
        return

    async def _fake_prices(event_id, seat_ids):
        return {sid: SEAT_PRICE for sid in seat_ids}

    monkeypatch.setattr("app.booking_service._fetch_seat_prices", _fake_prices)


@pytest_asyncio.fixture()
async def file_session_factory(tmp_path):
    """Session factory over a file-backed SQLite DB.

    Unlike `db_session`, writes here really commit, so tests can verify that
    production code paths persist their changes (checked via a fresh session).
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'booking.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    await engine.dispose()


@pytest_asyncio.fixture()
async def committing_client(file_session_factory, fake_redis) -> AsyncGenerator[AsyncClient, None]:
    """HTTP client whose get_db commits/rolls back exactly like production."""

    async def _override_get_db():
        async with file_session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = _override_get_db
    app.state.redis = fake_redis

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", headers=internal_headers()
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


def make_token(user_id: uuid.UUID | None = None) -> str:
    sub = str(user_id or uuid.uuid4())
    payload = {"sub": sub}
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def auth_headers(user_id: uuid.UUID | None = None) -> dict:
    return {"Authorization": f"Bearer {make_token(user_id)}"}
