import uuid

import pytest


@pytest.mark.asyncio
async def test_plan_gathering_then_complete(client):
    conversation_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())

    # First call: partial info -> GATHERING with missing_fields.
    resp1 = await client.post(
        "/plan",
        json={
            "conversation_id": conversation_id,
            "user_id": user_id,
            "message": "I want to visit Tokyo, budget $3000.",
        },
    )
    assert resp1.status_code == 200
    body1 = resp1.json()
    assert body1["status"] == "GATHERING"
    assert "response_text" in body1 and body1["response_text"]
    assert set(body1["missing_fields"]) == {"start_date", "end_date"}
    assert body1["constraints"]["destination"] == "Tokyo"

    # Second call, same conversation: supply the missing dates -> COMPLETE.
    resp2 = await client.post(
        "/plan",
        json={
            "conversation_id": conversation_id,
            "user_id": user_id,
            "message": "Let's go for 3 days starting 2026-11-01.",
        },
    )
    assert resp2.status_code == 200
    body2 = resp2.json()
    assert body2["status"] == "COMPLETE"
    assert "response_text" in body2 and body2["response_text"]
    assert body2["itinerary_id"] is not None
    assert body2["itinerary"]["destination"] == "Tokyo"
    assert len(body2["itinerary"]["days"]) == 3


@pytest.mark.asyncio
async def test_plan_full_info_single_call_is_complete(client):
    conversation_id = str(uuid.uuid4())
    resp = await client.post(
        "/plan",
        json={
            "conversation_id": conversation_id,
            "user_id": str(uuid.uuid4()),
            "message": "Plan a trip to Rome from 2026-12-01 to 2026-12-03.",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "COMPLETE"
    assert body["itinerary"]["destination"] == "Rome"


@pytest.mark.asyncio
async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
