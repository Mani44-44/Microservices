import pytest

pytestmark = pytest.mark.asyncio


async def test_stats_empty(client):
    resp = await client.get("/stats")
    assert resp.status_code == 200
    assert resp.json() == {
        "total_traces": 0,
        "by_agent_type": {},
        "avg_duration_ms": 0.0,
        "total_estimated_cost_usd": 0.0,
        "guardrail_block_rate": 0.0,
        "error_rate": 0.0,
    }


async def test_stats_aggregates_across_traces(client):
    await client.post(
        "/traces",
        json={
            "agent_type": "travel_planner",
            "input_text": "hi",
            "output_text": "hi there",
            "total_duration_ms": 100,
            "mock_llm_calls": 1,
            "estimated_cost_usd": 0.0002,
        },
    )
    await client.post(
        "/traces",
        json={
            "agent_type": "support",
            "input_text": "help",
            "output_text": "sure",
            "total_duration_ms": 300,
            "mock_llm_calls": 2,
            "estimated_cost_usd": 0.0004,
            "guardrail_output_blocked": True,
            "error": "boom",
        },
    )

    resp = await client.get("/stats")
    data = resp.json()
    assert data["total_traces"] == 2
    assert data["by_agent_type"] == {"travel_planner": 1, "support": 1}
    assert data["avg_duration_ms"] == pytest.approx(200.0)
    assert data["total_estimated_cost_usd"] == pytest.approx(0.0006)
    assert data["guardrail_block_rate"] == pytest.approx(0.5)
    assert data["error_rate"] == pytest.approx(0.5)
