import pytest
from fakeredis import aioredis as fake_aioredis

from app.rate_limit import check_and_increment


@pytest.mark.asyncio
async def test_allows_up_to_limit():
    r = fake_aioredis.FakeRedis(decode_responses=True)
    user_id = "user-1"
    for _ in range(20):
        assert await check_and_increment(r, user_id) is True
    await r.aclose()


@pytest.mark.asyncio
async def test_blocks_after_limit():
    r = fake_aioredis.FakeRedis(decode_responses=True)
    user_id = "user-2"
    for _ in range(20):
        await check_and_increment(r, user_id)
    assert await check_and_increment(r, user_id) is False
    await r.aclose()


@pytest.mark.asyncio
async def test_separate_users_have_separate_buckets():
    r = fake_aioredis.FakeRedis(decode_responses=True)
    for _ in range(20):
        await check_and_increment(r, "user-a")
    assert await check_and_increment(r, "user-a") is False
    assert await check_and_increment(r, "user-b") is True
    await r.aclose()
