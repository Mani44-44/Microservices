from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check(db: AsyncSession = Depends(get_db)) -> dict:
    """Return service health including database connectivity."""
    db_ok = False
    db_error: str | None = None
    try:
        await db.execute(text("SELECT 1"))
        db_ok = True
    except Exception as exc:
        db_error = str(exc)
        logger.error("health_db_check_failed", error=db_error)

    status = "healthy" if db_ok else "degraded"
    return {
        "status": status,
        "service": "catalog-service",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": {
            "database": {
                "status": "ok" if db_ok else "error",
                "error": db_error,
            }
        },
    }
