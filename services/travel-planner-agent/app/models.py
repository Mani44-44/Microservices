import uuid
from datetime import datetime

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Itinerary(Base):
    """One draft/complete itinerary per conversation."""

    __tablename__ = "itineraries"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    conversation_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        unique=True,
        index=True,
    )
    destination: Mapped[str] = mapped_column(String(255), nullable=True)
    start_date: Mapped[str] = mapped_column(Date, nullable=True)
    end_date: Mapped[str] = mapped_column(Date, nullable=True)
    budget: Mapped[float] = mapped_column(Numeric(10, 2), nullable=True)
    # Full constraint dict (destination/dates/budget/travelers/interests/
    # dietary_restrictions/etc) — the single source of truth ConstraintExtractor
    # reads and merges into on every /plan call.
    preferences: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # DRAFT -> COMPLETE -> BOOKED
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="DRAFT",
        server_default="DRAFT",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        onupdate=func.now(),
        nullable=True,
    )

    items: Mapped[list["ItineraryItem"]] = relationship(
        "ItineraryItem",
        back_populates="itinerary",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Itinerary id={self.id} status={self.status}>"


class ItineraryItem(Base):
    """A single day-plan slot (activity/restaurant/event/etc)."""

    __tablename__ = "itinerary_items"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    itinerary_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("itineraries.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    day_number: Mapped[int] = mapped_column(Integer, nullable=False)
    time_slot: Mapped[str] = mapped_column(String(50), nullable=False)  # Morning/Lunch/...
    # TRANSPORT/ACCOMMODATION/ACTIVITY/RESTAURANT/EVENT
    activity_type: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    location: Mapped[str] = mapped_column(String(255), nullable=True)
    cost_estimate: Mapped[float] = mapped_column(Numeric(10, 2), nullable=True)
    booking_url: Mapped[str] = mapped_column(String(500), nullable=True)
    source_reference: Mapped[str] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    itinerary: Mapped["Itinerary"] = relationship(
        "Itinerary",
        back_populates="items",
    )

    def __repr__(self) -> str:
        return f"<ItineraryItem day={self.day_number} title={self.title}>"
