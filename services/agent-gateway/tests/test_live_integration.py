"""
Live integration test: calls the REAL Travel Planner (8008) and Support
Agent (8009) services over HTTP. These are being built in parallel by other
subagents and may not be running yet - each test skips gracefully with a
clear message if its service is unreachable, rather than failing the suite.
"""
import uuid

import httpx
import pytest

from app.config import get_settings

settings = get_settings()


def _reachable(url: str) -> bool:
    try:
        httpx.get(f"{url}/health", timeout=1.0)
        return True
    except Exception:
        return False


@pytest.mark.asyncio
async def test_real_travel_planner_plan_endpoint():
    if not _reachable(settings.travel_planner_url):
        pytest.skip(
            f"Travel Planner Agent ({settings.travel_planner_url}) is not reachable - "
            "skipping live integration test."
        )
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{settings.travel_planner_url}/plan",
            json={
                "conversation_id": str(uuid.uuid4()),
                "user_id": str(uuid.uuid4()),
                "message": "Plan a 3 day trip to Tokyo",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "response_text" in body, f"Expected 'response_text' field in Travel Planner response, got: {body}"


@pytest.mark.asyncio
async def test_real_support_agent_handle_endpoint():
    if not _reachable(settings.support_agent_url):
        pytest.skip(
            f"Support Agent ({settings.support_agent_url}) is not reachable - "
            "skipping live integration test."
        )
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{settings.support_agent_url}/handle",
            json={
                "conversation_id": str(uuid.uuid4()),
                "user_id": str(uuid.uuid4()),
                "message": "What's my booking status?",
                "intent": "BOOKING_INQUIRY",
                "user_bearer_token": "dummy-token-for-live-test",
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert "response_text" in body, f"Expected 'response_text' field in Support Agent response, got: {body}"
