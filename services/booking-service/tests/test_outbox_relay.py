"""Self-check for the outbox relay: an unpublished row gets produced once and marked published."""
import json
import uuid
from unittest.mock import AsyncMock

import pytest

from app.models import OutboxEvent
from app.outbox_relay import publish_batch

pytestmark = pytest.mark.asyncio


async def test_publish_batch_publishes_and_marks_row(db_session):
    booking_id = str(uuid.uuid4())
    row = OutboxEvent(
        event_type="SEATS_LOCKED",
        payload={"booking_id": booking_id, "status": "PENDING"},
    )
    db_session.add(row)
    await db_session.commit()

    producer = AsyncMock()

    published = await publish_batch(producer, db_session)

    assert published == 1
    producer.send_and_wait.assert_awaited_once()
    _, kwargs = producer.send_and_wait.call_args
    envelope = json.loads(kwargs["value"])
    assert envelope["event_type"] == "SEATS_LOCKED"
    assert envelope["schema_version"] == 1
    assert envelope["payload"]["booking_id"] == booking_id
    assert kwargs["key"] == booking_id.encode("utf-8")

    await db_session.refresh(row)
    assert row.published_at is not None


async def test_publish_batch_noop_when_nothing_unpublished(db_session):
    producer = AsyncMock()
    published = await publish_batch(producer, db_session)
    assert published == 0
    producer.send_and_wait.assert_not_awaited()
