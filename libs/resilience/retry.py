"""Retry a startup connection attempt with exponential backoff.

Every service currently does ONE connect attempt on startup (DB engine.begin(),
Kafka consumer/producer .start()) with no retry. If the dependency isn't up yet
(e.g. after a host sleep, postgres is slow to become ready), the process exits
and docker-compose's `restart: on-failure` doesn't re-check
`depends_on: condition: service_healthy` on restart -- so the container just
crash-loops until the dependency happens to be ready by luck. This wraps the
connect call so it retries in-process instead.
"""

import asyncio
import random

import structlog

log = structlog.get_logger()


async def retry_with_backoff(
    fn,
    attempts: int = 5,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
    jitter: bool = True,
):
    """Call `fn()` (an async callable, no args), retrying on `exceptions`.

    Delay doubles each attempt (base_delay, 2x, 4x, ...) capped at max_delay,
    with up to 25% random jitter. Raises the last exception once `attempts`
    are exhausted. Succeeds on the first try with zero delay, so this is a
    no-op in the normal (dependency already up / test) case.
    """
    for attempt in range(1, attempts + 1):
        try:
            return await fn()
        except exceptions as exc:
            if attempt == attempts:
                raise
            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            if jitter:
                delay += random.uniform(0, delay * 0.25)
            log.warning(
                "retry_with_backoff_attempt_failed",
                attempt=attempt,
                attempts=attempts,
                delay=round(delay, 2),
                error=str(exc),
            )
            await asyncio.sleep(delay)
