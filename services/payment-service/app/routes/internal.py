"""
routes/internal.py
~~~~~~~~~~~~~~~~~~~
Internal endpoints called by other services (e.g. the Support Agent).
No authentication required - these must NOT be exposed publicly.
"""
import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Payment

log = structlog.get_logger()

router = APIRouter(prefix="/internal", tags=["internal"])


@router.get(
    "/payments/by-booking/{booking_id}",
    summary="Look up the most recent payment for a booking (internal use only)",
)
async def get_payment_by_booking(booking_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Payment)
        .where(Payment.booking_id == booking_id)
        .order_by(Payment.created_at.desc())
        .limit(1)
    )
    payment = result.scalar_one_or_none()
    if payment is None:
        raise HTTPException(status_code=404, detail={"error": {"message": "No payment found for this booking"}})
    return {
        "payment_id": str(payment.id),
        "booking_id": str(payment.booking_id),
        "amount": float(payment.amount),
        "currency": payment.currency,
        "status": payment.status,
    }
