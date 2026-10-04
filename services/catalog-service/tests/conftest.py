"""
Pytest configuration and shared fixtures for the catalog-service test suite.

Uses an in-memory SQLite database (via aiosqlite) so no live PostgreSQL is
needed during tests.  httpx.AsyncClient is used to drive the FastAPI app.
"""
from __future__ import annotations

import uuid
from decimal import Decimal
from typing import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.main import app
from app.models import Event, EventSeatPrice, Seat, Section, Venue
from libs.security import internal_headers

# ---------------------------------------------------------------------------
# Engine / session – SQLite in-memory for speed
# ---------------------------------------------------------------------------

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture(scope="session")
async def engine():
    eng = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture(scope="session")
async def session_factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


@pytest_asyncio.fixture()
async def db(session_factory) -> AsyncIterator[AsyncSession]:
    async with session_factory() as session:
        yield session
        await session.rollback()


# ---------------------------------------------------------------------------
# Override FastAPI dependency
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def client(session_factory) -> AsyncIterator[AsyncClient]:
    """Async test client with DB dependency overridden to use SQLite session."""

    async def override_get_db():
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport, base_url="http://test", headers=internal_headers()
    ) as ac:
        yield ac
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def venue(db: AsyncSession) -> Venue:
    """Create and persist a test Venue with two sections."""
    v = Venue(
        id=uuid.uuid4(),
        name="Test Arena",
        address="1 Main St",
        city="NYC",
        country="US",
        capacity=1000,
    )
    db.add(v)
    await db.flush()

    sec_floor = Section(
        id=uuid.uuid4(),
        venue_id=v.id,
        name="Floor",
        capacity=100,
        price_multiplier=Decimal("2.0"),
    )
    sec_upper = Section(
        id=uuid.uuid4(),
        venue_id=v.id,
        name="Upper Bowl",
        capacity=400,
        price_multiplier=Decimal("1.0"),
    )
    db.add_all([sec_floor, sec_upper])
    await db.flush()

    # Create a few seats in each section
    seats_floor = [
        Seat(
            id=uuid.uuid4(),
            section_id=sec_floor.id,
            seat_number=str(n),
            row_number="A",
            is_accessible=False,
        )
        for n in range(1, 6)
    ]
    seats_upper = [
        Seat(
            id=uuid.uuid4(),
            section_id=sec_upper.id,
            seat_number=str(n),
            row_number="B",
            is_accessible=False,
        )
        for n in range(1, 6)
    ]
    db.add_all(seats_floor + seats_upper)
    await db.commit()

    # Attach sections and seats to the venue object for convenience
    v._sections_data = [sec_floor, sec_upper]
    v._floor_seats = seats_floor
    v._upper_seats = seats_upper
    return v


@pytest_asyncio.fixture()
async def published_event(db: AsyncSession, venue: Venue) -> Event:
    """Create a published concert event and seat prices."""
    ev = Event(
        id=uuid.uuid4(),
        venue_id=venue.id,
        title="Rock Night",
        description="Epic rock concert",
        event_date=__import__("datetime").datetime(2026, 10, 15, 20, 0, 0),
        event_type="concert",
        status="PUBLISHED",
        base_price=Decimal("100.00"),
        currency="USD",
    )
    db.add(ev)
    await db.flush()

    # Add seat prices for floor seats
    for seat in venue._floor_seats:
        db.add(EventSeatPrice(
            id=uuid.uuid4(),
            event_id=ev.id,
            seat_id=seat.id,
            price=Decimal("200.00"),
        ))
    await db.commit()
    return ev


@pytest_asyncio.fixture()
async def draft_event(db: AsyncSession, venue: Venue) -> Event:
    """Create a draft event (should not appear in default list)."""
    ev = Event(
        id=uuid.uuid4(),
        venue_id=venue.id,
        title="Upcoming Show",
        description="TBD",
        event_date=__import__("datetime").datetime(2027, 1, 1, 20, 0, 0),
        event_type="concert",
        status="DRAFT",
        base_price=Decimal("80.00"),
        currency="USD",
    )
    db.add(ev)
    await db.commit()
    return ev
