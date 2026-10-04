"""Pytest configuration and shared fixtures for the User Service test suite.

Uses an in-process SQLite database (via aiosqlite) so tests have zero external
dependencies and run fully offline.
"""

import asyncio
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from libs.security import internal_headers

# ---------------------------------------------------------------------------
# SQLite test engine
# ---------------------------------------------------------------------------
# Use a *single* shared in-memory SQLite database across all connections so
# that tables created by one connection are visible to another.
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture(scope="session")
async def test_db_engine():
    """Create the SQLite engine once per test session."""
    engine = create_async_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False,
    )
    # Create schema
    async with engine.begin() as conn:
        # Import models so metadata is populated
        import app.models  # noqa: F401

        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def test_session(test_db_engine) -> AsyncGenerator[AsyncSession, None]:
    """Yield a fresh session that is rolled back after each test."""
    session_factory = async_sessionmaker(
        bind=test_db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_factory() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def client(test_db_engine) -> AsyncGenerator[AsyncClient, None]:
    """Yield an AsyncClient wired to the FastAPI app with the test DB injected."""
    session_factory = async_sessionmaker(
        bind=test_db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db

    # Bypass the lifespan (init_db) to avoid touching the real Postgres
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", headers=internal_headers()
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Data fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def test_user_data() -> dict:
    """Return a dict with valid registration fields."""
    return {
        "email": "alice@example.com",
        "password": "securepassword123",
        "full_name": "Alice Example",
        "phone": "+1-555-0100",
    }
