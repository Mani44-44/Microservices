import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Venue
from app.schemas import VenueDetailResponse, VenueResponse

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/venues", tags=["venues"])


@router.get("/", response_model=list[VenueResponse])
async def list_venues(db: AsyncSession = Depends(get_db)) -> list[VenueResponse]:
    """Return a list of all venues."""
    result = await db.execute(select(Venue).order_by(Venue.name))
    venues = result.scalars().all()
    return [VenueResponse.model_validate(v) for v in venues]


@router.get("/{venue_id}", response_model=VenueDetailResponse)
async def get_venue(
    venue_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> VenueDetailResponse:
    """Return a single venue with its sections."""
    result = await db.execute(
        select(Venue)
        .options(selectinload(Venue.sections))
        .where(Venue.id == venue_id)
    )
    venue = result.scalar_one_or_none()
    if not venue:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": {
                    "code": "VENUE_NOT_FOUND",
                    "message": f"Venue {venue_id} not found",
                    "details": {},
                }
            },
        )
    return VenueDetailResponse.model_validate(venue)
