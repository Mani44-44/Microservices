import uuid
from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import auth_headers


def _mocked_downstream():
    return (
        patch("app.orchestrator.clients.check_guardrail_input", new=AsyncMock(
            return_value={"blocked": False, "reasons": [], "redacted_text": ""}
        )),
        patch("app.orchestrator.clients.check_guardrail_output", new=AsyncMock(
            side_effect=lambda text, context_chunks=None: {"blocked": False, "redacted_text": text}
        )),
        patch("app.orchestrator.clients.send_trace", new=AsyncMock()),
    )


@pytest.mark.asyncio
async def test_list_and_get_conversation(client):
    headers = auth_headers()
    m1, m2, m3 = _mocked_downstream()
    with m1, m2, m3:
        chat_resp = await client.post(
            "/api/v1/agent/chat", json={"message": "Hello"}, headers=headers
        )
    conv_id = chat_resp.json()["conversation_id"]

    list_resp = await client.get("/api/v1/agent/conversations", headers=headers)
    assert list_resp.status_code == 200
    ids = [c["id"] for c in list_resp.json()]
    assert conv_id in ids

    detail_resp = await client.get(f"/api/v1/agent/conversations/{conv_id}", headers=headers)
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert len(detail["messages"]) == 2  # USER + AGENT


@pytest.mark.asyncio
async def test_get_conversation_not_owned_returns_404(client):
    headers = auth_headers()
    m1, m2, m3 = _mocked_downstream()
    with m1, m2, m3:
        chat_resp = await client.post(
            "/api/v1/agent/chat", json={"message": "Hello"}, headers=headers
        )
    conv_id = chat_resp.json()["conversation_id"]

    other_headers = auth_headers(uuid.uuid4())
    resp = await client.get(f"/api/v1/agent/conversations/{conv_id}", headers=other_headers)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_end_conversation(client):
    headers = auth_headers()
    m1, m2, m3 = _mocked_downstream()
    with m1, m2, m3:
        chat_resp = await client.post(
            "/api/v1/agent/chat", json={"message": "Hello"}, headers=headers
        )
    conv_id = chat_resp.json()["conversation_id"]

    resp = await client.delete(f"/api/v1/agent/conversations/{conv_id}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ENDED"


@pytest.mark.asyncio
async def test_escalate_conversation(client):
    headers = auth_headers()
    m1, m2, m3 = _mocked_downstream()
    with m1, m2, m3:
        chat_resp = await client.post(
            "/api/v1/agent/chat", json={"message": "Hello"}, headers=headers
        )
    conv_id = chat_resp.json()["conversation_id"]

    resp = await client.post(
        f"/api/v1/agent/conversations/{conv_id}/escalate",
        json={"reason": "user requested a human"},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "ESCALATED"


@pytest.mark.asyncio
async def test_chat_requires_auth(client):
    resp = await client.post("/api/v1/agent/chat", json={"message": "Hello"})
    assert resp.status_code in (401, 403)


@pytest.mark.asyncio
async def test_health_no_auth(client):
    resp = await client.get("/api/v1/agent/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
