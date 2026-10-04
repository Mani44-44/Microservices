"""
seed_data.py
============
Idempotent seed script for the TicketFlow catalog database.

Usage (from the repo root):
    python scripts/seed_data.py

It will:
  1. Create 2 venues with sections and seats.
  2. Create 5 events (4 published, 1 draft).
  3. Create EventSeatPrice rows for every seat at every published event.

Running the script a second time is safe – existing records are detected
and skipped.
"""
from __future__ import annotations

import asyncio
import math
import string
import sys
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

# Make sure the catalog-service package is importable when running from repo root.
SERVICE_DIR = Path(__file__).resolve().parent.parent / "services" / "catalog-service"
sys.path.insert(0, str(SERVICE_DIR))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# ---------------------------------------------------------------------------
# Settings – inline to avoid importing app.config before sys.path is set
# ---------------------------------------------------------------------------

DATABASE_URL = (
    "postgresql+asyncpg://ticketflow:ticketflow123@localhost:5440/ticketflow_catalog"
)

engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


# ---------------------------------------------------------------------------
# Import models AFTER sys.path adjustment
# ---------------------------------------------------------------------------

from app.models import Event, EventSeatPrice, Seat, Section, Venue  # noqa: E402
from app.database import Base  # noqa: E402


# ---------------------------------------------------------------------------
# Seat generation helper
# ---------------------------------------------------------------------------

def _generate_seats(section_id: uuid.UUID, capacity: int) -> list[Seat]:
    """
    Generate ``capacity`` Seat objects for a section distributed across rows A-Z.
    Each row gets up to 26 seats (numbered 1-26).
    """
    seats: list[Seat] = []
    rows = list(string.ascii_uppercase)  # A-Z
    seats_per_row = min(26, math.ceil(capacity / len(rows)))
    remaining = capacity

    for row in rows:
        if remaining <= 0:
            break
        count = min(seats_per_row, remaining)
        for n in range(1, count + 1):
            seats.append(
                Seat(
                    id=uuid.uuid4(),
                    section_id=section_id,
                    seat_number=str(n),
                    row_number=row,
                    is_accessible=(n == 1 and row == "A"),  # mark first seat accessible
                )
            )
        remaining -= count

    return seats


# ---------------------------------------------------------------------------
# Main seed logic
# ---------------------------------------------------------------------------

async def seed() -> None:
    print("TicketFlow – Catalog Seed Script")
    print("=" * 50)

    # Ensure tables exist
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("[OK] Tables verified / created.")

    async with SessionLocal() as db:
        # ------------------------------------------------------------------ #
        # 1. Venues                                                            #
        # ------------------------------------------------------------------ #

        # Check if venues already exist
        existing_venues = (await db.execute(select(Venue))).scalars().all()
        if existing_venues:
            print(f"[SKIP] Found {len(existing_venues)} existing venue(s) – skipping venue/section/seat creation.")
        else:
            print("Creating venues, sections and seats …")

            # --- Madison Square Arena ---
            msa = Venue(
                id=uuid.uuid4(),
                name="Madison Square Arena",
                address="4 Pennsylvania Plaza",
                city="NYC",
                country="US",
                capacity=5000,
            )
            db.add(msa)
            await db.flush()

            msa_sections_def = [
                ("Floor",       500,  Decimal("2.0")),
                ("Lower Bowl", 1500,  Decimal("1.5")),
                ("Upper Bowl", 2500,  Decimal("1.0")),
                ("VIP",         500,  Decimal("3.0")),
            ]
            msa_sections: list[Section] = []
            for name, cap, mult in msa_sections_def:
                sec = Section(
                    id=uuid.uuid4(),
                    venue_id=msa.id,
                    name=name,
                    capacity=cap,
                    price_multiplier=mult,
                )
                db.add(sec)
                await db.flush()
                msa_sections.append(sec)
                seats = _generate_seats(sec.id, cap)
                db.add_all(seats)
                print(f"  + MSA / {name}: {len(seats)} seats")

            # --- Orpheum Theater ---
            orpheum = Venue(
                id=uuid.uuid4(),
                name="Orpheum Theater",
                address="126 Second Ave",
                city="NYC",
                country="US",
                capacity=500,
            )
            db.add(orpheum)
            await db.flush()

            orpheum_sections_def = [
                ("Orchestra",  200, Decimal("2.0")),
                ("Mezzanine",  150, Decimal("1.5")),
                ("Balcony",    150, Decimal("1.0")),
            ]
            orpheum_sections: list[Section] = []
            for name, cap, mult in orpheum_sections_def:
                sec = Section(
                    id=uuid.uuid4(),
                    venue_id=orpheum.id,
                    name=name,
                    capacity=cap,
                    price_multiplier=mult,
                )
                db.add(sec)
                await db.flush()
                orpheum_sections.append(sec)
                seats = _generate_seats(sec.id, cap)
                db.add_all(seats)
                print(f"  + Orpheum / {name}: {len(seats)} seats")

            await db.commit()
            print("[OK] Venues, sections and seats committed.")

        # ------------------------------------------------------------------ #
        # 2. Events                                                            #
        # ------------------------------------------------------------------ #

        existing_events = (await db.execute(select(Event))).scalars().all()
        if existing_events:
            print(f"[SKIP] Found {len(existing_events)} existing event(s) – skipping event creation.")
        else:
            print("Creating events …")

            # Refresh venue objects (may have been committed in same session or different run)
            venues_result = await db.execute(select(Venue).order_by(Venue.name))
            venues = venues_result.scalars().all()
            venue_by_name: dict[str, Venue] = {v.name: v for v in venues}

            msa_id = venue_by_name["Madison Square Arena"].id
            orpheum_id = venue_by_name["Orpheum Theater"].id

            events_def = [
                dict(
                    title="Taylor Swift - Eras Tour",
                    event_type="concert",
                    status="PUBLISHED",
                    base_price=Decimal("150.00"),
                    event_date=datetime(2026, 10, 15, 20, 0, 0, tzinfo=timezone.utc),
                    venue_id=msa_id,
                    description="The record-breaking Eras Tour comes to NYC.",
                ),
                dict(
                    title="Coldplay Live",
                    event_type="concert",
                    status="PUBLISHED",
                    base_price=Decimal("120.00"),
                    event_date=datetime(2026, 11, 20, 20, 0, 0, tzinfo=timezone.utc),
                    venue_id=msa_id,
                    description="Coldplay's Music of the Spheres World Tour.",
                ),
                dict(
                    title="NY Knicks vs Lakers",
                    event_type="sports",
                    status="PUBLISHED",
                    base_price=Decimal("200.00"),
                    event_date=datetime(2026, 9, 30, 19, 30, 0, tzinfo=timezone.utc),
                    venue_id=msa_id,
                    description="Regular season NBA game.",
                ),
                dict(
                    title="Hamilton Musical",
                    event_type="theater",
                    status="PUBLISHED",
                    base_price=Decimal("180.00"),
                    event_date=datetime(2026, 10, 5, 19, 0, 0, tzinfo=timezone.utc),
                    venue_id=orpheum_id,
                    description="The Tony Award-winning musical by Lin-Manuel Miranda.",
                ),
                dict(
                    title="Upcoming Concert",
                    event_type="concert",
                    status="DRAFT",
                    base_price=Decimal("100.00"),
                    event_date=datetime(2027, 1, 1, 21, 0, 0, tzinfo=timezone.utc),
                    venue_id=msa_id,
                    description="Details coming soon.",
                ),
            ]

            created_events: list[Event] = []
            for ev_def in events_def:
                ev = Event(id=uuid.uuid4(), currency="USD", **ev_def)
                db.add(ev)
                created_events.append(ev)
                print(f"  + Event: {ev.title} [{ev.status}]")

            await db.flush()

            # ------------------------------------------------------------------ #
            # 3. EventSeatPrices for published events                            #
            # ------------------------------------------------------------------ #

            print("Creating EventSeatPrice rows for published events …")

            for ev in created_events:
                if ev.status != "PUBLISHED":
                    print(f"  - Skipping pricing for DRAFT event: {ev.title}")
                    continue

                # Load sections for venue
                sections_result = await db.execute(
                    select(Section).where(Section.venue_id == ev.venue_id)
                )
                sections = sections_result.scalars().all()

                total_prices = 0
                for section in sections:
                    seats_result = await db.execute(
                        select(Seat).where(Seat.section_id == section.id)
                    )
                    seats = seats_result.scalars().all()
                    price = (ev.base_price * section.price_multiplier).quantize(Decimal("0.01"))
                    for seat in seats:
                        db.add(EventSeatPrice(
                            id=uuid.uuid4(),
                            event_id=ev.id,
                            seat_id=seat.id,
                            price=price,
                        ))
                        total_prices += 1

                print(f"  + {ev.title}: {total_prices} seat prices created")

            await db.commit()
            print("[OK] Events and seat prices committed.")

    print("=" * 50)
    print("Seed complete!")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
