import uuid

import pytest

pytestmark = pytest.mark.asyncio


async def _post_trace(client, **overrides):
    body = {
        "agent_type": "travel_planner",
        "input_text": "Plan a trip to Paris",
        "output_text": "Here is your itinerary",
        "steps": [{"name": "intent_classification", "duration_ms": 120, "metadata": {}}],
        "guardrail_input_blocked": False,
        "guardrail_output_blocked": False,
        "total_duration_ms": 450,
        "mock_llm_calls": 3,
        "error": None,
    }
    body.update(overrides)
    return await client.post("/traces", json=body)


async def test_create_trace_generates_id_when_omitted(client):
    resp = await _post_trace(client)
    assert resp.status_code == 200
    data = resp.json()
    assert uuid.UUID(data["trace_id"])


async def test_create_trace_uses_supplied_trace_id(client):
    trace_id = str(uuid.uuid4())
    resp = await _post_trace(client, trace_id=trace_id)
    assert resp.status_code == 200
    assert resp.json()["trace_id"] == trace_id


async def test_create_trace_computes_simulated_cost_from_mock_calls(client):
    trace_id = str(uuid.uuid4())
    await _post_trace(client, trace_id=trace_id, mock_llm_calls=5, estimated_cost_usd=None)

    resp = await client.get(f"/traces/{trace_id}")
    assert resp.status_code == 200
    # 5 * 0.0002 default cost-per-mock-call
    assert resp.json()["estimated_cost_usd"] == pytest.approx(0.001)


async def test_create_trace_respects_explicit_cost(client):
    trace_id = str(uuid.uuid4())
    await _post_trace(client, trace_id=trace_id, mock_llm_calls=5, estimated_cost_usd=9.99)

    resp = await client.get(f"/traces/{trace_id}")
    assert resp.json()["estimated_cost_usd"] == pytest.approx(9.99)


async def test_get_trace_not_found(client):
    resp = await client.get(f"/traces/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert "error" in resp.json()


async def test_get_trace_roundtrips_steps_and_flags(client):
    trace_id = str(uuid.uuid4())
    await _post_trace(
        client,
        trace_id=trace_id,
        guardrail_input_blocked=True,
        error="something failed",
    )

    resp = await client.get(f"/traces/{trace_id}")
    data = resp.json()
    assert data["guardrail_input_blocked"] is True
    assert data["error"] == "something failed"
    assert data["steps"][0]["name"] == "intent_classification"


async def test_list_traces_newest_first_and_limit(client):
    for _ in range(3):
        await _post_trace(client)

    resp = await client.get("/traces?limit=2")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["created_at"] >= data[1]["created_at"]


async def test_list_traces_filters_by_agent_type(client):
    await _post_trace(client, agent_type="support")
    resp = await client.get("/traces?agent_type=support")
    assert resp.status_code == 200
    assert all(t["agent_type"] == "support" for t in resp.json())


async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
