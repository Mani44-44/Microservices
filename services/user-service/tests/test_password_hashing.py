"""bcrypt is deliberately slow (~0.2s at cost 12). It must run off the event
loop, and login must spend that time whether or not the email exists."""
import asyncio
import uuid
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.asyncio


async def test_register_does_not_block_the_event_loop(client):
    import time

    stamps: list[float] = []
    stop = asyncio.Event()

    async def ticker():
        while not stop.is_set():
            stamps.append(time.perf_counter())
            await asyncio.sleep(0.005)

    task = asyncio.create_task(ticker())
    resp = await client.post(
        "/register",
        json={
            "email": f"loop-{uuid.uuid4().hex[:8]}@example.com",
            "password": "correct-horse-battery",
            "full_name": "Loop Test",
        },
    )
    stop.set()
    await task

    assert resp.status_code == 201
    longest_stall = max(b - a for a, b in zip(stamps, stamps[1:]))
    # A blocking bcrypt call (~0.2s at cost 12) would stall the loop that long.
    assert longest_stall < 0.1, f"event loop stalled for {longest_stall:.3f}s"


async def test_login_with_unknown_email_still_verifies_a_hash(client):
    import app.routes.users as users

    real_verify = users.verify_password
    calls = []

    def spy(password, hashed):
        calls.append(hashed)
        return real_verify(password, hashed)

    with patch.object(users, "verify_password", spy):
        resp = await client.post(
            "/login", json={"email": f"nobody-{uuid.uuid4().hex[:8]}@example.com", "password": "whatever-123"}
        )

    assert resp.status_code == 401
    assert len(calls) == 1, "unknown emails must cost the same bcrypt work as known ones"
