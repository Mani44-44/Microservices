"""
routes/plan.py
~~~~~~~~~~~~~~
POST /plan — the single endpoint the Agent Gateway calls. Internal use
only, no auth (the gateway has already authenticated the end user).
"""
from datetime import date

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.constraint_extractor import ConstraintExtractor
from app.database import get_db
from app.itinerary_builder import build_itinerary
from app.llm import get_llm_client
from app.models import Itinerary, ItineraryItem
from app.schemas import PlanRequest, PlanResponse

log = structlog.get_logger()
router = APIRouter(tags=["plan"])

extractor = ConstraintExtractor()


def _missing_fields_message(missing: list[str]) -> str:
    friendly = {
        "destination": "your destination",
        "start_date": "your travel start date",
        "end_date": "your travel end date",
    }
    parts = [friendly.get(f, f) for f in missing]
    if len(parts) == 1:
        joined = parts[0]
    else:
        joined = ", ".join(parts[:-1]) + " and " + parts[-1]
    return f"Great! To plan your trip, could you tell me {joined}?"


@router.post("/plan", response_model=PlanResponse)
async def plan(body: PlanRequest, db: AsyncSession = Depends(get_db), llm_client=Depends(get_llm_client)):
    settings = get_settings()

    result = await db.execute(
        select(Itinerary).where(Itinerary.conversation_id == body.conversation_id)
    )
    itinerary = result.scalar_one_or_none()
    if itinerary is None:
        itinerary = Itinerary(
            user_id=body.user_id,
            conversation_id=body.conversation_id,
            preferences={},
            status="DRAFT",
        )
        db.add(itinerary)

    existing_constraints = dict(itinerary.preferences or {})
    merged = extractor.extract(body.message, existing_constraints)
    missing = merged.pop("missing_required_fields")

    itinerary.preferences = merged
    itinerary.destination = merged.get("destination")
    if merged.get("start_date"):
        itinerary.start_date = date.fromisoformat(merged["start_date"])
    if merged.get("end_date"):
        itinerary.end_date = date.fromisoformat(merged["end_date"])
    if merged.get("budget") is not None:
        itinerary.budget = merged["budget"]

    if missing:
        await db.flush()
        return PlanResponse(
            response_text=_missing_fields_message(missing),
            status="GATHERING",
            constraints=merged,
            missing_fields=missing,
        )

    item_dicts, day_summaries, response_text = await build_itinerary(
        destination=merged["destination"],
        start_date=merged["start_date"],
        end_date=merged["end_date"],
        interests=merged.get("interests"),
        dietary_restrictions=merged.get("dietary_restrictions"),
        rag_service_url=settings.rag_service_url,
        llm_client=llm_client,
    )

    # Replace any previously persisted items for this itinerary (e.g. a
    # constraint changed on a later message) with the freshly built plan.
    itinerary.items.clear()
    for item_dict in item_dicts:
        itinerary.items.append(ItineraryItem(**item_dict))

    itinerary.status = "COMPLETE"

    await db.flush()

    return PlanResponse(
        response_text=response_text,
        status="COMPLETE",
        constraints=merged,
        itinerary_id=str(itinerary.id),
        itinerary={"destination": merged["destination"], "days": day_summaries},
    )
