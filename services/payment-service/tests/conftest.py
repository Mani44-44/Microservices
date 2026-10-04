"""
Test configuration and shared fixtures for the Payment Service.

Uses an in-process SQLite database so tests run without a real PostgreSQL
instance.  SQLAlchemy's aiosqlite dialect is used for async support.
"""
import asyncio
import uuid
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.database import Base, get_db
from app.main import app
from libs.security import internal_headers


# ---------------------------------------------------------------------------
# Event-loop scope (pytest-asyncio >= 0.21 requires explicit declaration)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def event_loop_policy():
    return asyncio.DefaultEventLoopPolicy()


# ---------------------------------------------------------------------------
# In-memory SQLite engine (one per test session)
# ---------------------------------------------------------------------------

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
    return async_sessionmaker(
        test_engine,
        expire_on_commit=False,
        class_=AsyncSession,
    )


# ---------------------------------------------------------------------------
# Per-test database session with automatic rollback for isolation
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Override FastAPI's get_db dependency
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """HTTP client with the DB dependency overridden to use the test session."""

    async def _override_get_db():
        # A fresh Session per request, bound to the same connection/transaction
        # as db_session, so concurrent requests (see test_race_condition_idempotency)
        # don't share one AsyncSession instance -- AsyncSession isn't safe for
        # concurrent use by multiple in-flight requests.
        session = AsyncSession(bind=db_session.bind, expire_on_commit=False)
        try:
            yield session
            await session.flush()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", headers=internal_headers()
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------


def unique_idempotency_key() -> str:
    return f"idem-{uuid.uuid4().hex}"
