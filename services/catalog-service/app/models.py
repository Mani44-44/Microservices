import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Venue(Base):
    """Physical venue where events are held."""

    __tablename__ = "venues"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    address: Mapped[str | None] = mapped_column(String(500))
    city: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    country: Mapped[str] = mapped_column(String(100), nullable=False)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Relationships
    events: Mapped[list["Event"]] = relationship("Event", back_populates="venue")
    sections: Mapped[list["Section"]] = relationship("Section", back_populates="venue")


class Event(Base):
    """An event taking place at a venue."""

    __tablename__ = "events"

    __table_args__ = (
        Index("ix_events_event_date", "event_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    venue_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("venues.id", ondelete="SET NULL"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    event_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    doors_open: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    event_type: Mapped[str | None] = mapped_column(
        String(50)
    )  # 'concert', 'sports', 'theater'
    status: Mapped[str] = mapped_column(
        String(20), default="DRAFT", nullable=False
    )  # DRAFT / PUBLISHED / CANCELLED / COMPLETED
    base_price: Mapped[float | None] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(3), default="USD", nullable=False)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now()
    )

    # Relationships
    venue: Mapped["Venue | None"] = relationship("Venue", back_populates="events")
    seat_prices: Mapped[list["EventSeatPrice"]] = relationship(
        "EventSeatPrice", back_populates="event"
    )


class Section(Base):
    """A named section within a venue (e.g. Floor, VIP, Upper Bowl)."""

    __tablename__ = "sections"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    venue_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("venues.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    capacity: Mapped[int | None] = mapped_column(Integer)
    price_multiplier: Mapped[float] = mapped_column(
        Numeric(5, 2), default=1.0, nullable=False
    )

    # Relationships
    venue: Mapped["Venue"] = relationship("Venue", back_populates="sections")
    seats: Mapped[list["Seat"]] = relationship("Seat", back_populates="section")


class Seat(Base):
    """An individual seat within a section."""

    __tablename__ = "seats"

    __table_args__ = (
        UniqueConstraint("section_id", "seat_number", "row_number", name="uq_seat_in_section"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    section_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sections.id", ondelete="CASCADE"), nullable=False
    )
    seat_number: Mapped[str] = mapped_column(String(20), nullable=False)
    row_number: Mapped[str | None] = mapped_column(String(10))
    is_accessible: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # Relationships
    section: Mapped["Section"] = relationship("Section", back_populates="seats")
    seat_prices: Mapped[list["EventSeatPrice"]] = relationship(
        "EventSeatPrice", back_populates="seat"
    )


class EventSeatPrice(Base):
    """Price override for a specific seat at a specific event."""

    __tablename__ = "event_seat_prices"

    __table_args__ = (
        UniqueConstraint("event_id", "seat_id", name="uq_event_seat"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("events.id", ondelete="CASCADE"), nullable=False
    )
    seat_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("seats.id", ondelete="CASCADE"), nullable=False
    )
    price: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)

    # Relationships
    event: Mapped["Event"] = relationship("Event", back_populates="seat_prices")
    seat: Mapped["Seat"] = relationship("Seat", back_populates="seat_prices")
