import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Payment(Base):
    """Represents a payment intent and its lifecycle."""

    __tablename__ = "payments"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    booking_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    amount: Mapped[float] = mapped_column(
        Numeric(10, 2),
        nullable=False,
    )
    currency: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="USD",
        server_default="USD",
    )
    # PENDING | SUCCEEDED | FAILED | REFUNDED
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="PENDING",
        server_default="PENDING",
    )
    provider: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="mock",
        server_default="mock",
    )
    provider_payment_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    # Unique key supplied by caller to ensure exactly-once creation
    idempotency_key: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    error_message: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationship
    refunds: Mapped[list["Refund"]] = relationship(
        "Refund",
        back_populates="payment",
        lazy="selectin",
    )

    __table_args__ = (
        Index("ix_payments_booking_id", "booking_id"),
        Index("ix_payments_idempotency_key", "idempotency_key", unique=True),
    )


class Refund(Base):
    """Represents a refund against a succeeded payment."""

    __tablename__ = "refunds"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    payment_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("payments.id", ondelete="CASCADE"),
        nullable=False,
    )
    amount: Mapped[float] = mapped_column(
        Numeric(10, 2),
        nullable=False,
    )
    reason: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )
    # PENDING | SUCCEEDED | FAILED
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="PENDING",
        server_default="PENDING",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationship
    payment: Mapped["Payment"] = relationship(
        "Payment",
        back_populates="refunds",
    )
