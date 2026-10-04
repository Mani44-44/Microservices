"""
replay_dlq.py
=============
Replay messages stuck on a service's dead-letter topic back onto its main
`booking.events` topic, using libs/messaging/dlq_replay.py.

Usage (from the repo root):
    python scripts/replay_dlq.py --service notification
    python scripts/replay_dlq.py --service analytics --max-messages 10

Run this after fixing whatever bug sent messages to the DLQ in the first
place — it does not retry or transform anything, just puts the backlog back
in line for the consumer to process normally.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

_SERVICE_DIRS = {
    "notification": REPO_ROOT / "services" / "notification-service",
    "analytics": REPO_ROOT / "services" / "analytics-service",
}


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service", required=True, choices=sorted(_SERVICE_DIRS))
    parser.add_argument("--max-messages", type=int, default=None)
    args = parser.parse_args()

    sys.path.insert(0, str(_SERVICE_DIRS[args.service]))
    from app.config import get_settings  # noqa: E402
    from app.consumer import DLQ_TOPIC, TOPIC  # noqa: E402

    from libs.messaging import run_replay

    settings = get_settings()
    replayed = await run_replay(
        dlq_topic=DLQ_TOPIC,
        target_topic=TOPIC,
        kafka_brokers=settings.kafka_brokers,
        max_messages=args.max_messages,
    )
    print(f"Replayed {replayed} message(s) from {DLQ_TOPIC} to {TOPIC}.")


if __name__ == "__main__":
    asyncio.run(main())
