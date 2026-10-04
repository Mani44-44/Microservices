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
from app.main import app
from libs.llm.groq_client import FakeGroqClient, ToolCallResult
from libs.security import internal_headers

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest.fixture(scope="session")
def event_loop_policy():
    return asyncio.DefaultEventLoopPolicy()


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


@pytest.fixture()
def fake_llm_client() -> FakeGroqClient:
    """Default: LLM judge never blocks. Tests that care about a specific
    verdict overwrite app.state.llm_client (or this fixture's .default_tool
    / .tool_responses) before making their request. Real GroqClient is never
    constructed in tests -- lifespan() is not run by ASGITransport."""
    return FakeGroqClient(
        default_tool=ToolCallResult(
            name="guardrail_verdict",
            arguments={"blocked": False, "category": "benign", "reason": "default fake verdict"},
        )
    )


@pytest_asyncio.fixture()
async def client(db_session, fake_llm_client) -> AsyncGenerator[AsyncClient, None]:
    async def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    app.state.llm_client = fake_llm_client
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", headers=internal_headers()
    ) as ac:
        yield ac
    app.dependency_overrides.clear()
    app.state.llm_client = None
