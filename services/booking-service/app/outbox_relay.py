import asyncio
import json
from datetime import datetime, timezone

import structlog
from aiokafka import AIOKafkaProducer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import async_session_factory
from app.models import OutboxEvent

log = structlog.get_logger()
settings = get_settings()

TOPIC = "booking.events"
SCHEMA_VERSION = 1


def _serialize(row: OutboxEvent) -> bytes:
    envelope = {
        "schema_version": SCHEMA_VERSION,
        "event_type": row.event_type,
        "event_id": str(row.id),
        "occurred_at": row.created_at.isoformat(),
        "payload": row.payload,
    }
    return json.dumps(envelope).encode("utf-8")


def _key(row: OutboxEvent) -> bytes | None:
    booking_id = row.payload.get("booking_id") if isinstance(row.payload, dict) else None
    return str(booking_id).encode("utf-8") if booking_id else None


async def publish_batch(producer: AIOKafkaProducer, db: AsyncSession) -> int:
    """Publish one batch of unpublished outbox rows. Returns count published."""
    result = await db.execute(
        select(OutboxEvent)
        .where(OutboxEvent.published_at.is_(None))
        .order_by(OutboxEvent.created_at)
        .limit(settings.outbox_batch_size)
    )
    rows = result.scalars().all()
    if not rows:
        return 0

    for row in rows:
        await producer.send_and_wait(TOPIC, value=_serialize(row), key=_key(row))
        row.published_at = datetime.now(timezone.utc)

    await db.commit()
    return len(rows)


async def outbox_relay_loop(producer: AIOKafkaProducer) -> None:
    """Background task: poll unpublished outbox rows and publish them to Kafka."""
    while True:
        await asyncio.sleep(settings.outbox_poll_interval_seconds)
        try:
            async with async_session_factory() as db:
                published = await publish_batch(producer, db)
            if published:
                log.info("outbox_relay_published", count=published)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("outbox_relay_error", error=str(exc))
