from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class CreatePaymentIntentRequest(BaseModel):
    """Payload for creating a new payment intent."""

    booking_id: UUID
    amount: Decimal = Field(..., gt=0, description="Payment amount (must be > 0)")
    currency: str = Field(default="USD", max_length=10)
    idempotency_key: str = Field(..., min_length=1, max_length=255)
    user_id: UUID | None = None


class ConfirmPaymentRequest(BaseModel):
    """Payload to confirm/charge an existing pending payment."""

    payment_method_details: str = Field(
        ...,
        min_length=1,
        description="Opaque string representing the payment method (card token, etc.)",
    )


class RefundRequest(BaseModel):
    """Payload to request a refund against a succeeded payment."""

    amount: Decimal = Field(..., gt=0, description="Refund amount (must be > 0)")
    reason: str = Field(default="", max_length=500)


class WebhookPayload(BaseModel):
    """Inbound webhook event from the mock payment provider."""

    payment_id: str
    event: str
    amount: Decimal
    status: str
    timestamp: str


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------


class PaymentResponse(BaseModel):
    """Serialised Payment returned to callers."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    booking_id: UUID
    user_id: UUID | None = None
    amount: Decimal
    currency: str
    status: str
    provider: str
    provider_payment_id: str | None = None
    idempotency_key: str
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime | None = None


class RefundResponse(BaseModel):
    """Serialised Refund returned to callers."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    payment_id: UUID
    amount: Decimal
    reason: str | None = None
    status: str
    created_at: datetime
    processed_at: datetime | None = None


# ---------------------------------------------------------------------------
# Error schemas
# ---------------------------------------------------------------------------


class ErrorDetail(BaseModel):
    code: str
    message: str
    details: dict = Field(default_factory=dict)
    correlation_id: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorDetail
