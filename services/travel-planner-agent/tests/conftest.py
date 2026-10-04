"""
Test configuration and shared fixtures for the Travel Planner Agent.

Uses an in-process SQLite database (no real PostgreSQL) so tests run
without any external services.
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

from app.database import Base, get_db
from app.llm import get_llm_client
from app.main import app
from libs.llm.groq_client import FakeGroqClient
from libs.security import internal_headers


@pytest.fixture(scope="session")
def event_loop_policy():
    return asyncio.DefaultEventLoopPolicy()


TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture()
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


@pytest_asyncio.fixture()
async def test_session_factory(test_engine):
    return async_sessionmaker(test_engine, expire_on_commit=False, class_=AsyncSession)


@pytest_asyncio.fixture()
async def db_session(test_engine, test_session_factory) -> AsyncGenerator[AsyncSession, None]:
    async with test_engine.connect() as conn:
        await conn.begin()
        session = AsyncSession(bind=conn, expire_on_commit=False)
        try:
            yield session
        finally:
            await session.close()
            await conn.rollback()


@pytest.fixture()
def fake_llm() -> FakeGroqClient:
    """Never hits the real Groq API — used for every test via dependency override."""
    return FakeGroqClient(default_text="Here's your itinerary: a great trip awaits.")


@pytest_asyncio.fixture()
async def client(db_session: AsyncSession, fake_llm: FakeGroqClient) -> AsyncGenerator[AsyncClient, None]:
    """HTTP client with DB overridden with a fast, in-process SQLite session.

    Uses the *same* session for the whole test (not a fresh one per request)
    so a two-call test (GATHERING then COMPLETE) sees its own writes.
    """

    async def _override_get_db():
        try:
            yield db_session
            await db_session.flush()
        except Exception:
            await db_session.rollback()
            raise

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_llm_client] = lambda: fake_llm

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", headers=internal_headers()
    ) as ac:
        yield ac
    app.dependency_overrides.clear()
