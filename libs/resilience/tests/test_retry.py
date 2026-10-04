import pytest

from libs.resilience import retry_with_backoff


@pytest.mark.asyncio
async def test_succeeds_before_exhausting_attempts():
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ConnectionError("not ready yet")
        return "ok"

    result = await retry_with_backoff(flaky, attempts=5, base_delay=0.01, max_delay=0.02)

    assert result == "ok"
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_raises_last_exception_after_exhausting_attempts():
    calls = {"n": 0}

    async def always_fails():
        calls["n"] += 1
        raise ConnectionError("still not ready")

    with pytest.raises(ConnectionError):
        await retry_with_backoff(always_fails, attempts=4, base_delay=0.01, max_delay=0.02)

    assert calls["n"] == 4
