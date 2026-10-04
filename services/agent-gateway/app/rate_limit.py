"""
rate_limit.py
~~~~~~~~~~~~~
Fixed-window rate limiter: 20 messages/hour/user via Redis INCR on
ratelimit:{user_id}:{current_hour_bucket}, with an ~3700s EXPIRE.

ponytail: fixed-window counter (not sliding window) — allows brief bursts
across a bucket boundary. Upgrade path: sliding-window/token-bucket if that
matters later.
"""
import time

import redis.asyncio as aioredis

from app.config import get_settings

settings = get_settings()


def _bucket_key(user_id: str) -> str:
    hour_bucket = int(time.time() // 3600)
    return f"ratelimit:{user_id}:{hour_bucket}"


async def check_and_increment(redis_client: aioredis.Redis, user_id: str) -> bool:
    """Returns True if the request is allowed, False if rate-limited."""
    key = _bucket_key(user_id)
    count = await redis_client.incr(key)
    if count == 1:
        await redis_client.expire(key, 3700)
    return count <= settings.rate_limit_per_hour
