import uuid
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.mock_provider import MockPaymentProvider, PaymentOutcome
from app.models import Payment, Refund
from app.schemas import (
    ConfirmPaymentRequest,
    CreatePaymentIntentRequest,
    PaymentResponse,
    RefundRequest,
    RefundResponse,
    WebhookPayload,
)

log = structlog.get_logger()
router = APIRouter(prefix="/payments", tags=["payments"])
settings = get_settings()

# One shared provider instance – settings are injected at startup
_provider = MockPaymentProvider(
    success_rate=settings.payment_success_rate,
    decline_rate=settings.payment_decline_rate,
    timeout_delay=settings.payment_timeout_delay,
)


def _correlation_id(request: Request) -> str:
    return request.state.correlation_id if hasattr(request.state, "correlation_id") else str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------


@router.get("/health", summary="Health check")
async def health():
    return {"status": "ok", "service": settings.service_name}


# ---------------------------------------------------------------------------
# Create payment intent
# ---------------------------------------------------------------------------


@router.post(
    "/create-intent",
    response_model=PaymentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a payment intent (idempotent)",
)
async def create_payment_intent(
    body: CreatePaymentIntentRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    correlation_id = _correlation_id(request)
    log.info(
        "create_payment_intent",
        booking_id=str(body.booking_id),
        idempotency_key=body.idempotency_key,
        amount=str(body.amount),
        correlation_id=correlation_id,
    )

    # --- Idempotency check ---------------------------------------------------
    result = await db.execute(
        select(Payment).where(Payment.idempotency_key == body.idempotency_key)
    )
    existing = result.scalar_one_or_none()
    if existing:
        log.info(
            "idempotency_hit",
            idempotency_key=body.idempotency_key,
            payment_id=str(existing.id),
            correlation_id=correlation_id,
        )
        return existing

    # --- Create new payment record -------------------------------------------
    payment = Payment(
        id=uuid.uuid4(),
        booking_id=body.booking_id,
        user_id=body.user_id,
        amount=body.amount,
        currency=body.currency,
        status="PENDING",
        provider="mock",
        idempotency_key=body.idempotency_key,
    )
    db.add(payment)
    try:
        await db.flush()
    except IntegrityError:
        # Lost the race to a concurrent request with the same idempotency_key.
        await db.rollback()
        result = await db.execute(
            select(Payment).where(Payment.idempotency_key == body.idempotency_key)
        )
        existing = result.scalar_one_or_none()
        if existing:
            log.info(
                "idempotency_race_lost",
                idempotency_key=body.idempotency_key,
                payment_id=str(existing.id),
                correlation_id=correlation_id,
            )
            return existing
        raise

    log.info(
        "payment_intent_created",
        payment_id=str(payment.id),
        booking_id=str(body.booking_id),
        correlation_id=correlation_id,
    )
    return payment


# ---------------------------------------------------------------------------
# Confirm payment
# ---------------------------------------------------------------------------


@router.post(
    "/{payment_id}/confirm",
    response_model=PaymentResponse,
    summary="Confirm and process a pending payment",
)
async def confirm_payment(
    payment_id: uuid.UUID,
    body: ConfirmPaymentRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    correlation_id = _correlation_id(request)
    log.info(
        "confirm_payment",
        payment_id=str(payment_id),
        correlation_id=correlation_id,
    )

    result = await db.execute(select(Payment).where(Payment.id == payment_id))
    payment = result.scalar_one_or_none()
    if not payment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "PAYMENT_NOT_FOUND",
                    "message": f"Payment {payment_id} not found",
                    "details": {},
                    "correlation_id": correlation_id,
                }
            },
        )

    if payment.status != "PENDING":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": {
                    "code": "PAYMENT_NOT_PENDING",
                    "message": f"Payment is in status '{payment.status}', cannot confirm",
                    "details": {"current_status": payment.status},
                    "correlation_id": correlation_id,
                }
            },
        )

    # --- Call mock provider --------------------------------------------------
    result_obj = await _provider.process_payment(
        amount=float(payment.amount),
        payment_method=body.payment_method_details,
    )

    payment.updated_at = datetime.now(timezone.utc)

    if result_obj.outcome in (PaymentOutcome.SUCCESS, PaymentOutcome.TIMEOUT_SUCCESS):
        payment.status = "SUCCEEDED"
        payment.provider_payment_id = result_obj.provider_payment_id
        log.info(
            "payment_succeeded",
            payment_id=str(payment.id),
            provider_payment_id=result_obj.provider_payment_id,
            outcome=result_obj.outcome,
            correlation_id=correlation_id,
        )
    else:
        payment.status = "FAILED"
        payment.provider_payment_id = result_obj.provider_payment_id
        payment.error_message = result_obj.error_message
        log.warning(
            "payment_failed",
            payment_id=str(payment.id),
            error_message=result_obj.error_message,
            correlation_id=correlation_id,
        )

    await db.flush()
    return payment


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------


@router.post(
    "/webhook",
    summary="Receive payment webhook events from the mock provider",
)
async def receive_webhook(
    body: WebhookPayload,
    request: Request,
    db: AsyncSession = Depends(get_db),
    x_webhook_signature: str | None = Header(default=None, alias="X-Webhook-Signature"),
):
    correlation_id = _correlation_id(request)

    # --- Verify HMAC-SHA256 signature ----------------------------------------
    if not x_webhook_signature:
        log.warning("webhook_missing_signature", correlation_id=correlation_id)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "MISSING_SIGNATURE",
                    "message": "X-Webhook-Signature header is required",
                    "details": {},
                    "correlation_id": correlation_id,
                }
            },
        )

    payload_dict = body.model_dump(mode="json")
    valid = _provider.verify_webhook_signature(
        payload=payload_dict,
        signature=x_webhook_signature,
        secret=settings.webhook_secret,
    )
    if not valid:
        log.warning(
            "webhook_invalid_signature",
            payment_id=body.payment_id,
            correlation_id=correlation_id,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "INVALID_SIGNATURE",
                    "message": "Webhook signature verification failed",
                    "details": {},
                    "correlation_id": correlation_id,
                }
            },
        )

    # --- Update payment status -----------------------------------------------
    try:
        payment_uuid = uuid.UUID(body.payment_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "INVALID_PAYMENT_ID",
                    "message": "payment_id is not a valid UUID",
                    "details": {},
                    "correlation_id": correlation_id,
                }
            },
        )

    result = await db.execute(select(Payment).where(Payment.id == payment_uuid))
    payment = result.scalar_one_or_none()
    if payment:
        payment.status = body.status
        payment.updated_at = datetime.now(timezone.utc)
        log.info(
            "webhook_payment_updated",
            payment_id=body.payment_id,
            new_status=body.status,
            webhook_event=body.event,
            correlation_id=correlation_id,
        )
    else:
        log.warning(
            "webhook_payment_not_found",
            payment_id=body.payment_id,
            correlation_id=correlation_id,
        )

    return {"received": True, "event": body.event}


# ---------------------------------------------------------------------------
# Refund
# ---------------------------------------------------------------------------


@router.post(
    "/{payment_id}/refund",
    response_model=RefundResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Refund a succeeded payment",
)
async def refund_payment(
    payment_id: uuid.UUID,
    body: RefundRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    correlation_id = _correlation_id(request)
    log.info(
        "refund_payment",
        payment_id=str(payment_id),
        amount=str(body.amount),
        correlation_id=correlation_id,
    )

    result = await db.execute(select(Payment).where(Payment.id == payment_id))
    payment = result.scalar_one_or_none()
    if not payment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "PAYMENT_NOT_FOUND",
                    "message": f"Payment {payment_id} not found",
                    "details": {},
                    "correlation_id": correlation_id,
                }
            },
        )

    if payment.status != "SUCCEEDED":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": {
                    "code": "PAYMENT_NOT_REFUNDABLE",
                    "message": f"Only SUCCEEDED payments can be refunded; current status: '{payment.status}'",
                    "details": {"current_status": payment.status},
                    "correlation_id": correlation_id,
                }
            },
        )

    if float(body.amount) > float(payment.amount):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": {
                    "code": "REFUND_EXCEEDS_PAYMENT",
                    "message": "Refund amount exceeds original payment amount",
                    "details": {
                        "refund_amount": str(body.amount),
                        "payment_amount": str(payment.amount),
                    },
                    "correlation_id": correlation_id,
                }
            },
        )

    now = datetime.now(timezone.utc)
    refund = Refund(
        id=uuid.uuid4(),
        payment_id=payment.id,
        amount=body.amount,
        reason=body.reason,
        status="SUCCEEDED",
        created_at=now,
        processed_at=now,
    )
    db.add(refund)

    # Mark payment as refunded
    payment.status = "REFUNDED"
    payment.updated_at = now

    await db.flush()

    log.info(
        "refund_succeeded",
        refund_id=str(refund.id),
        payment_id=str(payment.id),
        amount=str(body.amount),
        correlation_id=correlation_id,
    )
    return refund


# ---------------------------------------------------------------------------
# Get payment
# ---------------------------------------------------------------------------


@router.get(
    "/{payment_id}",
    response_model=PaymentResponse,
    summary="Retrieve a payment by ID",
)
async def get_payment(
    payment_id: uuid.UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    correlation_id = _correlation_id(request)
    result = await db.execute(select(Payment).where(Payment.id == payment_id))
    payment = result.scalar_one_or_none()
    if not payment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "PAYMENT_NOT_FOUND",
                    "message": f"Payment {payment_id} not found",
                    "details": {},
                    "correlation_id": correlation_id,
                }
            },
        )
    return payment
