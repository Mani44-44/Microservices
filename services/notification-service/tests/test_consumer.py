"""Idempotency self-check: the same envelope delivered twice is only processed once."""
import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from app.consumer import DLQ_TOPIC, TOPIC, handle_envelope, process_message
from app.models import Notification
from libs.messaging import replay_dlq

pytestmark = pytest.mark.asyncio


def _envelope(event_id: str, booking_id: str, user_id: str, event_type="BOOKING_CONFIRMED"):
    return {
        "schema_version": 1,
        "event_type": event_type,
        "event_id": event_id,
        "occurred_at": "2026-08-30T00:00:00+00:00",
        "payload": {
            "booking_id": booking_id,
            "user_id": user_id,
            "status": "CONFIRMED",
        },
    }


async def test_handle_envelope_creates_notification(db_session):
    envelope = _envelope(str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()))
    await handle_envelope(db_session, envelope)

    result = await db_session.execute(select(Notification))
    rows = result.scalars().all()
    assert len(rows) == 1
    assert rows[0].event_type == "BOOKING_CONFIRMED"


async def test_duplicate_delivery_is_processed_once(db_session):
    event_id = str(uuid.uuid4())
    envelope = _envelope(event_id, str(uuid.uuid4()), str(uuid.uuid4()))

    await handle_envelope(db_session, envelope)
    await handle_envelope(db_session, envelope)  # redelivered, same event_id

    result = await db_session.execute(select(Notification))
    rows = result.scalars().all()
    assert len(rows) == 1


async def test_unhandled_event_type_marks_processed_without_notification(db_session):
    envelope = _envelope(
        str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()), event_type="SEATS_LOCKED"
    )
    await handle_envelope(db_session, envelope)

    result = await db_session.execute(select(Notification))
    assert result.scalars().all() == []


async def test_process_message_routes_to_dlq_after_max_retries(db_session):
    envelope = _envelope(str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4()))
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
    """A message stuck on the DLQ must land back on the main topic when replayed."""
    msg = AsyncMock()
    msg.value = b'{"event_id": "abc"}'
    msg.key = b"some-key"

    consumer = AsyncMock()
    consumer.getmany.side_effect = [{"partition-0": [msg]}, {}]

    producer = AsyncMock()

    replayed = await replay_dlq(consumer, producer, TOPIC)

    assert replayed == 1
    producer.send_and_wait.assert_awaited_once_with(TOPIC, value=msg.value, key=msg.key)
    consumer.commit.assert_awaited_once()


async def test_replay_dlq_respects_max_messages():
    msgs = [AsyncMock(value=f"msg-{i}".encode(), key=None) for i in range(3)]
    consumer = AsyncMock()
    consumer.getmany.side_effect = [{"partition-0": msgs}]
    producer = AsyncMock()

    replayed = await replay_dlq(consumer, producer, TOPIC, max_messages=2)

    assert replayed == 2
    assert producer.send_and_wait.await_count == 2


async def test_dlq_topic_constant_is_the_replay_target():
    assert DLQ_TOPIC == "booking.events.dlq"
    assert TOPIC == "booking.events"
