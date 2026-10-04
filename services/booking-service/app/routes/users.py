"""
routes/users.py
~~~~~~~~~~~~~~~
User-scoped booking endpoints (paginated booking history).
"""
import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

import app.booking_service as svc
from app.auth import get_current_user_id, get_current_user_role
from app.database import get_db
from app.schemas import PaginatedBookings, BookingResponse

log = structlog.get_logger()

router = APIRouter(prefix="/users", tags=["users"])


@router.get(
    "/{user_id}/bookings",
    response_model=PaginatedBookings,
    summary="Get paginated booking history for a user",
)
async def get_user_bookings_endpoint(
    user_id: uuid.UUID,
    request: Request,
    current_user_id: uuid.UUID = Depends(get_current_user_id),
    current_user_role: str = Depends(get_current_user_role),
    db: AsyncSession = Depends(get_db),
    page: int = Query(default=1, ge=1, description="Page number (1-indexed)"),
    size: int = Query(default=20, ge=1, le=100, description="Items per page"),
):
    # Users can only fetch their own bookings, unless the caller is an admin
    # (e.g. support staff looking up a customer's booking history).
    if user_id != current_user_id and current_user_role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": {
                    "code": "FORBIDDEN",
                    "message": "You can only view your own bookings",
                    "details": {},
                }
            },
        )

    bookings, total = await svc.get_user_bookings(user_id, page, size, db)

    return PaginatedBookings(
        items=[BookingResponse.model_validate(b) for b in bookings],
        total=total,
        page=page,
        size=size,
    )
