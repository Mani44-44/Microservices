"""
lock_manager.py
~~~~~~~~~~~~~~~
Redis-based distributed seat locking using Lua scripts for atomicity.

All seat-lock operations are performed as atomic Lua scripts to eliminate
race conditions between the check-and-set steps.
"""
import uuid
from typing import List

import redis.asyncio as aioredis
import structlog

from app.config import get_settings

log = structlog.get_logger()
settings = get_settings()

# ---------------------------------------------------------------------------
# Lua scripts
# ---------------------------------------------------------------------------

# Atomically lock ALL seats or fail entirely.
# Returns {1, 0} on success or {0, i} where i is the 1-based index of
# the first seat that was already locked.
LOCK_SEATS_SCRIPT = """
local user_id = ARGV[1]
local ttl = tonumber(ARGV[2])
local n = #KEYS
-- First pass: check all seats are free
for i = 1, n do
    if redis.call('EXISTS', KEYS[i]) == 1 then
        return {0, i}  -- seat at index i is taken
    end
end
-- Second pass: atomically set all locks
for i = 1, n do
    redis.call('SET', KEYS[i], user_id, 'EX', ttl)
end
return {1, 0}  -- success
"""

# Atomically release all seat locks owned by user.
# Returns the number of locks that were released.
RELEASE_SEATS_SCRIPT = """
local user_id = ARGV[1]
local released = 0
for i = 1, #KEYS do
    if redis.call('GET', KEYS[i]) == user_id then
        redis.call('DEL', KEYS[i])
        released = released + 1
    end
end
return released
"""

# Get lock info for multiple seats in a single round-trip.
# Returns a flat list: [locked_by_1, ttl_1, locked_by_2, ttl_2, ...]
GET_LOCKS_SCRIPT = """
local result = {}
for i = 1, #KEYS do
    local val = redis.call('GET', KEYS[i])
    local ttl = redis.call('TTL', KEYS[i])
    table.insert(result, val or '')
    table.insert(result, tostring(ttl))
end
return result
"""


# ---------------------------------------------------------------------------
# LockManager
# ---------------------------------------------------------------------------


class LockManager:
    """Manages distributed Redis locks for event seats."""

    def __init__(self, redis_client: aioredis.Redis) -> None:
        self.redis = redis_client

    # ------------------------------------------------------------------
    # Key helpers
    # ------------------------------------------------------------------

    def _seat_lock_key(self, event_id: uuid.UUID, seat_id: uuid.UUID) -> str:
        return f"seat_lock:{event_id}:{seat_id}"

    def _user_lock_count_key(self, user_id: uuid.UUID) -> str:
        return f"user_lock_count:{user_id}"

    def _booking_lock_key(self, booking_id: uuid.UUID) -> str:
        return f"booking_lock:{booking_id}"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def lock_seats(
        self,
        event_id: uuid.UUID,
        seat_ids: List[uuid.UUID],
        user_id: uuid.UUID,
        ttl: int,
    ) -> tuple[bool, int]:
        """Atomically lock all seats or fail without side effects.

        Args:
            event_id:  Event the seats belong to.
            seat_ids:  List of seat UUIDs to lock.
            user_id:   The user claiming the seats (stored as lock value).
            ttl:       Lock expiry in seconds.

        Returns:
            (True, 0)  on success.
            (False, i) where i is the 1-based index of the first seat that
                       was already taken.
        """
        keys = [self._seat_lock_key(event_id, sid) for sid in seat_ids]
        try:
            result = await self.redis.eval(
                LOCK_SEATS_SCRIPT,
                len(keys),
                *keys,
                str(user_id),
                ttl,
            )
            success = result[0] == 1
            failed_idx = int(result[1])
            if success:
                log.info(
                    "seats_locked",
                    event_id=str(event_id),
                    seat_count=len(seat_ids),
                    user_id=str(user_id),
                )
            else:
                log.warning(
                    "seat_lock_failed",
                    event_id=str(event_id),
                    failed_seat_idx=failed_idx,
                    user_id=str(user_id),
                )
            return success, failed_idx
        except Exception as exc:
            log.error("redis_lock_error", error=str(exc))
            raise

    async def release_seats(
        self,
        event_id: uuid.UUID,
        seat_ids: List[uuid.UUID],
        user_id: uuid.UUID,
    ) -> int:
        """Atomically release all seat locks owned by *user_id*.

        Locks owned by other users are left untouched.

        Returns:
            Number of locks actually released.
        """
        keys = [self._seat_lock_key(event_id, sid) for sid in seat_ids]
        if not keys:
            return 0
        try:
            count = await self.redis.eval(
                RELEASE_SEATS_SCRIPT,
                len(keys),
                *keys,
                str(user_id),
            )
            log.info(
                "seats_released",
                event_id=str(event_id),
                released_count=int(count),
                user_id=str(user_id),
            )
            return int(count)
        except Exception as exc:
            log.error("redis_release_error", error=str(exc))
            raise

    async def release_seats_admin(
        self,
        event_id: uuid.UUID,
        seat_ids: List[uuid.UUID],
    ) -> int:
        """Force-release seat locks regardless of owner (used by expiry handler)."""
        pipe = self.redis.pipeline()
        for seat_id in seat_ids:
            key = self._seat_lock_key(event_id, seat_id)
            pipe.delete(key)
        results = await pipe.execute()
        released = sum(1 for r in results if r)
        log.info(
            "seats_force_released",
            event_id=str(event_id),
            released_count=released,
        )
        return released

    async def get_locked_seats(
        self,
        event_id: uuid.UUID,
        seat_ids: List[uuid.UUID],
    ) -> dict:
        """Get lock status for specific seats.

        Returns:
            Dict mapping str(seat_id) -> {'locked_by': user_id_str, 'ttl': seconds}.
        """
        keys = [self._seat_lock_key(event_id, sid) for sid in seat_ids]
        if not keys:
            return {}
        result = await self.redis.eval(GET_LOCKS_SCRIPT, len(keys), *keys)
        status: dict = {}
        for i, seat_id in enumerate(seat_ids):
            raw_locked_by = result[i * 2]
            raw_ttl = result[i * 2 + 1]
            locked_by = (
                raw_locked_by.decode() if isinstance(raw_locked_by, bytes) else raw_locked_by
            )
            ttl = int(raw_ttl.decode() if isinstance(raw_ttl, bytes) else raw_ttl)
            if locked_by:
                status[str(seat_id)] = {"locked_by": locked_by, "ttl": ttl}
        return status

    async def extend_lock_ttl(
        self,
        event_id: uuid.UUID,
        seat_ids: List[uuid.UUID],
        user_id: uuid.UUID,
        new_ttl: int,
    ) -> bool:
        """Extend TTL on seat locks (e.g., during payment processing).

        Returns True if all extends succeeded.
        """
        pipe = self.redis.pipeline()
        for seat_id in seat_ids:
            key = self._seat_lock_key(event_id, seat_id)
            pipe.expire(key, new_ttl)
        results = await pipe.execute()
        success = all(results)
        log.info(
            "lock_ttl_extended",
            event_id=str(event_id),
            user_id=str(user_id),
            new_ttl=new_ttl,
            all_succeeded=success,
        )
        return success

    async def get_all_locked_seats_for_event(
        self, event_id: uuid.UUID
    ) -> List[str]:
        """Scan Redis for all currently locked seats for an event.

        Used by the internal API to power seat-map availability display.
        """
        pattern = f"seat_lock:{event_id}:*"
        locked: List[str] = []
        async for key in self.redis.scan_iter(match=pattern, count=100):
            raw = key.decode() if isinstance(key, bytes) else key
            seat_id = raw.split(":")[-1]
            locked.append(seat_id)
        return locked


# ---------------------------------------------------------------------------
# Dependency helper
# ---------------------------------------------------------------------------


def get_lock_manager(redis_client: aioredis.Redis) -> LockManager:
    return LockManager(redis_client)
