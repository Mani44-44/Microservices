"""Self-check: counts increment correctly, and duplicate delivery doesn't double-count."""
import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.consumer import DLQ_TOPIC, TOPIC, handle_envelope, process_message
from app.models import BookingEventCount
from libs.messaging import replay_dlq

pytestmark = pytest.mark.asyncio


def _envelope(event_id: str, event_type="BOOKING_CONFIRMED"):
    return {
        "schema_version": 1,
        "event_type": event_type,
        "event_id": event_id,
        "occurred_at": "2026-08-30T00:00:00+00:00",
        "payload": {"booking_id": str(uuid.uuid4())},
    }


async def test_handle_envelope_increments_count(db_session):
    await handle_envelope(db_session, _envelope(str(uuid.uuid4())))

    row = await db_session.get(BookingEventCount, "BOOKING_CONFIRMED")
    assert row.count == 1


async def test_process_message_routes_to_dlq_after_max_retries(db_session):
    envelope = _envelope(str(uuid.uuid4()))
    value = json.dumps(envelope).encode("utf-8")
    key = b"some-key"
    producer = AsyncMock()

    with (
        patch("app.consumer.handle_envelope", AsyncMock(side_effect=RuntimeError("boom"))),
        patch("app.consumer.asyncio.sleep", AsyncMock()),
    ):
        await process_message(producer, value, key)

    producer.send_and_wait.assert_awaited_once_with("booking.events.dlq", value=value, key=key)


async def test_replay_dlq_republishes_to_main_topic():
    """A message stuck on analytics's DLQ must land back on the main topic when replayed."""
    msg = AsyncMock()
    msg.value = b'{"event_id": "abc"}'
    msg.key = None

    consumer = AsyncMock()
    consumer.getmany.side_effect = [{"partition-0": [msg]}, {}]
    producer = AsyncMock()

    replayed = await replay_dlq(consumer, producer, TOPIC)

    assert replayed == 1
    producer.send_and_wait.assert_awaited_once_with(TOPIC, value=msg.value, key=msg.key)


async def test_dlq_topic_constant_is_the_replay_target():
    assert DLQ_TOPIC == "booking.events.dlq"
    assert TOPIC == "booking.events"


async def test_multiple_distinct_events_accumulate(db_session):
    await handle_envelope(db_session, _envelope(str(uuid.uuid4())))
    await handle_envelope(db_session, _envelope(str(uuid.uuid4())))

    row = await db_session.get(BookingEventCount, "BOOKING_CONFIRMED")
    assert row.count == 2


async def test_duplicate_delivery_does_not_double_count(db_session):
    event_id = str(uuid.uuid4())
    envelope = _envelope(event_id)

    await handle_envelope(db_session, envelope)
    await handle_envelope(db_session, envelope)  # redelivered, same event_id

    row = await db_session.get(BookingEventCount, "BOOKING_CONFIRMED")
    assert row.count == 1
