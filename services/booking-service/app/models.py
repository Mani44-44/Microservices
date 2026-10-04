import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Booking(Base):
    """Core booking record, created when seats are locked."""

    __tablename__ = "bookings"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    # PENDING -> CONFIRMED / CANCELLED / EXPIRED / FAILED
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="PENDING",
        server_default="PENDING",
    )
    total_amount: Mapped[float] = mapped_column(
        Numeric(10, 2),
        nullable=True,
    )
    currency: Mapped[str] = mapped_column(
        String(3),
        nullable=False,
        default="USD",
        server_default="USD",
    )
    locked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    cancelled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
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

    # Relationships
    seats: Mapped[list["BookingSeat"]] = relationship(
        "BookingSeat",
        back_populates="booking",
        cascade="all, delete-orphan",
        lazy="selectin",
    )
    payment_attempts: Mapped[list["PaymentAttempt"]] = relationship(
        "PaymentAttempt",
        back_populates="booking",
        cascade="all, delete-orphan",
        lazy="selectin",
    )

    __table_args__ = (
        # Index for the expiry handler: find PENDING bookings that have expired
        Index("ix_bookings_status_expires_at", "status", "expires_at"),
    )

    def __repr__(self) -> str:
        return f"<Booking id={self.id} status={self.status}>"


class BookingSeat(Base):
    """Individual seat attached to a booking."""

    __tablename__ = "booking_seats"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    booking_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("bookings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    seat_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        nullable=False,
    )
    price_at_booking: Mapped[float] = mapped_column(
        Numeric(10, 2),
        nullable=True,
    )

    booking: Mapped["Booking"] = relationship(
        "Booking",
        back_populates="seats",
    )

    __table_args__ = (
        UniqueConstraint("booking_id", "seat_id", name="uq_booking_seat"),
    )

    def __repr__(self) -> str:
        return f"<BookingSeat booking={self.booking_id} seat={self.seat_id}>"


class PaymentAttempt(Base):
    """Records every attempt to charge for a booking (for idempotency)."""

    __tablename__ = "payment_attempts"

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    booking_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("bookings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    payment_intent_id: Mapped[str] = mapped_column(
        String(255),
        nullable=True,
    )
    amount: Mapped[float] = mapped_column(
        Numeric(10, 2),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=True,
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(255),
        nullable=True,
        unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    booking: Mapped["Booking"] = relationship(
        "Booking",
        back_populates="payment_attempts",
    )

    def __repr__(self) -> str:
        return f"<PaymentAttempt id={self.id} status={self.status}>"


class OutboxEvent(Base):
    """Transactional outbox for reliable event publishing."""

    __tablename__ = "outbox_events"
    __table_args__ = (
        Index("ix_outbox_published_at", "published_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    event_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    payload: Mapped[dict] = mapped_column(
        JSON,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    retry_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        server_default="0",
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<OutboxEvent type={self.event_type} id={self.id}>"
