import asyncio
import json
import uuid

import structlog
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from sqlalchemy.exc import IntegrityError

from app.config import get_settings
from app.database import async_session_factory
from app.models import BookingEventCount, ProcessedEvent
from libs.observability import DLQ_MESSAGES

log = structlog.get_logger()
settings = get_settings()

TOPIC = "booking.events"
DLQ_TOPIC = "booking.events.dlq"


async def _mark_processed_if_new(db, event_id: uuid.UUID) -> bool:
    """Returns True if this event_id was newly claimed (not seen before).

    Uses a SAVEPOINT so a duplicate-key rollback only undoes this insert,
    not any other work already pending on the session.
    """
    try:
        async with db.begin_nested():
            db.add(ProcessedEvent(event_id=event_id))
            await db.flush()
        return True
    except IntegrityError:
        return False


async def _increment_count(db, event_type: str) -> None:
    row = await db.get(BookingEventCount, event_type)
    if row is None:
        row = BookingEventCount(event_type=event_type, count=0)
        db.add(row)
    row.count += 1


async def handle_envelope(db, envelope: dict) -> None:
    event_id = uuid.UUID(envelope["event_id"])
    event_type = envelope["event_type"]

    if not await _mark_processed_if_new(db, event_id):
        await db.commit()
        log.info("event_already_processed", event_id=str(event_id))
        return

    await _increment_count(db, event_type)
    await db.commit()
    log.info("analytics_event_recorded", event_type=event_type)


async def process_message(producer, value: bytes, key: bytes | None) -> None:
    """Retry the handler up to kafka_max_retries times, then route to the DLQ topic."""
    envelope = json.loads(value)
    attempt = 0
    while True:
        attempt += 1
        try:
            async with async_session_factory() as db:
                await handle_envelope(db, envelope)
            return
        except Exception as exc:
            log.error(
                "analytics_handler_error",
                attempt=attempt,
                error=str(exc),
                event_id=envelope.get("event_id"),
            )
            if attempt >= settings.kafka_max_retries:
                await producer.send_and_wait(DLQ_TOPIC, value=value, key=key)
                DLQ_MESSAGES.labels(service=settings.service_name, topic=DLQ_TOPIC).inc()
                log.error("analytics_event_dlq", event_id=envelope.get("event_id"))
                return
            await asyncio.sleep(0.5 * attempt)


async def consume_loop() -> None:
    """Background task: consume booking.events, idempotently, with retry-then-DLQ.

    Reconnects on broker outages instead of letting the task die silently
    (asyncio.create_task swallows uncaught exceptions with no log line).
    """
    while True:
        consumer = AIOKafkaConsumer(
            TOPIC,
            bootstrap_servers=settings.kafka_brokers,
            group_id=settings.kafka_consumer_group,
            enable_auto_commit=False,
        )
        producer = AIOKafkaProducer(bootstrap_servers=settings.kafka_brokers)
        try:
            await consumer.start()
            await producer.start()
            async for msg in consumer:
                await process_message(producer, msg.value, msg.key)
                await consumer.commit()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.error("analytics_consumer_error", error=str(exc))
            await asyncio.sleep(2)
        finally:
            await consumer.stop()
            await producer.stop()
