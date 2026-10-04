"""Replay messages stuck on a dead-letter topic back onto the main topic.

Every consumer service in TicketFlow retries a failing handler then routes
the message to `<topic>.dlq` (see services/*/app/consumer.py) — but nothing
ever consumes that DLQ topic. This is the missing other half: once the bug
that caused the failure is fixed, replay puts the message back in line.
"""

import structlog
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

log = structlog.get_logger()


async def replay_dlq(
    consumer: AIOKafkaConsumer,
    producer: AIOKafkaProducer,
    target_topic: str,
    max_messages: int | None = None,
    poll_timeout_ms: int = 2000,
) -> int:
    """Drain `consumer` (already subscribed to a DLQ topic) onto `target_topic`.

    Uses bounded getmany() polls rather than `async for` so a one-shot CLI
    replay naturally stops once the backlog is drained, instead of blocking
    forever waiting for the next message. Caller owns start()/stop() of both
    consumer and producer, so this stays testable with fakes/mocks.
    """
    replayed = 0
    while True:
        batches = await consumer.getmany(timeout_ms=poll_timeout_ms)
        if not batches:
            break
        for messages in batches.values():
            for msg in messages:
                await producer.send_and_wait(target_topic, value=msg.value, key=msg.key)
                replayed += 1
                log.info("dlq_message_replayed", target_topic=target_topic, count=replayed)
                if max_messages is not None and replayed >= max_messages:
                    await consumer.commit()
                    return replayed
        await consumer.commit()
    return replayed


async def run_replay(
    dlq_topic: str,
    target_topic: str,
    kafka_brokers: str,
    max_messages: int | None = None,
) -> int:
    """Construct real Kafka consumer/producer, replay, and clean up."""
    consumer = AIOKafkaConsumer(
        dlq_topic,
        bootstrap_servers=kafka_brokers,
        group_id=f"{dlq_topic}-replay",
        enable_auto_commit=False,
        auto_offset_reset="earliest",
    )
    producer = AIOKafkaProducer(bootstrap_servers=kafka_brokers)
    await consumer.start()
    await producer.start()
    try:
        return await replay_dlq(consumer, producer, target_topic, max_messages)
    finally:
        await consumer.stop()
        await producer.stop()
