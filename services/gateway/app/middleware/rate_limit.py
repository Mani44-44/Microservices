"""
Sliding window rate limiter backed by Redis.

Uses a Lua script executed atomically on the Redis server to implement
a sliding window counter. Each request is stored as a sorted set member
scored by the current timestamp, with old entries pruned on each call.
"""

import time
import redis.asyncio as aioredis
import structlog

log = structlog.get_logger()


class RateLimiter:
    """
    Sliding window rate limiter using Redis sorted sets.

    Two separate limit buckets:
      - Per-IP:   guards unauthenticated + authenticated traffic alike
      - Per-User: guards authenticated traffic on a per-identity basis
    """

    # Atomically prunes old entries, counts current entries, and conditionally
    # adds a new entry — all in a single round-trip.
    SLIDING_WINDOW_SCRIPT = """
    local key = KEYS[1]
    local now = tonumber(ARGV[1])
    local window = tonumber(ARGV[2])
    local limit = tonumber(ARGV[3])
    local window_start = now - window

    -- Remove entries older than the window
    redis.call('ZREMRANGEBYSCORE', key, 0, window_start)

    local count = redis.call('ZCARD', key)
    if count >= limit then
        return {0, count}
    end

    -- Use score+random suffix to allow multiple requests at the same millisecond
    redis.call('ZADD', key, now, now .. '-' .. math.random(100000))
    redis.call('EXPIRE', key, window)
    return {1, count + 1}
    """

    def __init__(
        self,
        redis: aioredis.Redis,
        per_user_limit: int = 100,
        per_ip_limit: int = 300,
        window_seconds: int = 60,
    ) -> None:
        self.redis = redis
        self.per_user_limit = per_user_limit
        self.per_ip_limit = per_ip_limit
        self.window_seconds = window_seconds

    async def check_rate_limit(self, key: str, limit: int) -> tuple[bool, int]:
        """
        Run the sliding window check for a given key and limit.

        Returns:
            (allowed, current_count) — allowed is False when the limit is hit.
        """
        now = time.time()
        result = await self.redis.eval(
            self.SLIDING_WINDOW_SCRIPT,
            1,          # number of KEYS
            key,        # KEYS[1]
            now,        # ARGV[1]
            self.window_seconds,  # ARGV[2]
            limit,      # ARGV[3]
        )
        allowed = bool(result[0])
        count = int(result[1])
        log.debug(
            "rate_limit_check",
            key=key,
            allowed=allowed,
            count=count,
            limit=limit,
        )
        return allowed, count

    async def check_ip_limit(self, ip: str) -> tuple[bool, int]:
        """Check per-IP rate limit."""
        key = f"rate_limit:ip:{ip}"
        return await self.check_rate_limit(key, self.per_ip_limit)

    async def check_user_limit(self, user_id: str) -> tuple[bool, int]:
        """Check per-user rate limit."""
        key = f"rate_limit:user:{user_id}"
        return await self.check_rate_limit(key, self.per_user_limit)
