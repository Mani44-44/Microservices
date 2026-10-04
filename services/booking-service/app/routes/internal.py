"""
routes/internal.py
~~~~~~~~~~~~~~~~~~
Internal endpoints called by the API gateway or catalog service.
No authentication required – these must NOT be exposed publicly.
"""
import uuid

import structlog
from fastapi import APIRouter, Request

from app.lock_manager import LockManager
from app.schemas import LockedSeatsResponse

log = structlog.get_logger()

router = APIRouter(prefix="/internal", tags=["internal"])


def _get_lock_manager(request: Request) -> LockManager:
    from app.lock_manager import get_lock_manager

    return get_lock_manager(request.app.state.redis)


@router.get(
    "/events/{event_id}/locked-seats",
    response_model=LockedSeatsResponse,
    summary="Return all currently locked seat IDs for an event (internal use only)",
)
async def get_locked_seats_for_event(
    event_id: uuid.UUID,
    request: Request,
):
    lock_manager = _get_lock_manager(request)
    locked_seat_ids = await lock_manager.get_all_locked_seats_for_event(event_id)
    return LockedSeatsResponse(
        event_id=event_id,
        locked_seat_ids=locked_seat_ids,
        count=len(locked_seat_ids),
    )
