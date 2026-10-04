"""
clients.py
~~~~~~~~~~
Thin httpx wrappers around the downstream services this gateway calls.
Every call has a graceful fallback so the gateway keeps working standalone
if a sub-agent/service is unreachable or errors.
"""
import structlog
import httpx

from app.config import get_settings
from libs.security import internal_headers

log = structlog.get_logger()
settings = get_settings()


async def check_guardrail_input(text: str) -> dict:
    """POST {guardrail_service_url}/guardrails/check-input.

    On error, fails open (blocked=False) — a guardrail outage should not by
    itself make the whole agent gateway unusable, but it IS logged loudly.
    """
    url = f"{settings.guardrail_service_url}/guardrails/check-input"
    try:
        async with httpx.AsyncClient(timeout=settings.downstream_timeout_seconds, headers=internal_headers()) as client:
            resp = await client.post(url, json={"text": text})
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        log.error("guardrail_input_check_failed", error=str(exc))
        return {"blocked": False, "reasons": [], "redacted_text": text}


async def check_guardrail_output(text: str, context_chunks: list[str] | None = None) -> dict:
    url = f"{settings.guardrail_service_url}/guardrails/check-output"
    try:
        async with httpx.AsyncClient(timeout=settings.downstream_timeout_seconds, headers=internal_headers()) as client:
            resp = await client.post(
                url, json={"text": text, "context_chunks": context_chunks or []}
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        log.error("guardrail_output_check_failed", error=str(exc))
        return {"blocked": False, "redacted_text": text, "reasons": []}


async def call_travel_planner(conversation_id: str, user_id: str, message: str) -> dict:
    url = f"{settings.travel_planner_url}/plan"
    try:
        async with httpx.AsyncClient(timeout=settings.downstream_timeout_seconds, headers=internal_headers()) as client:
            resp = await client.post(
                url,
                json={"conversation_id": conversation_id, "user_id": user_id, "message": message},
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        log.error("travel_planner_call_failed", error=str(exc))
        return {"response_text": "The travel planner is temporarily unavailable."}


async def call_support_agent(
    conversation_id: str, user_id: str, message: str, intent: str, user_bearer_token: str
) -> dict:
    url = f"{settings.support_agent_url}/handle"
    try:
        async with httpx.AsyncClient(timeout=settings.downstream_timeout_seconds, headers=internal_headers()) as client:
            resp = await client.post(
                url,
                json={
                    "conversation_id": conversation_id,
                    "user_id": user_id,
                    "message": message,
                    "intent": intent,
                    "user_bearer_token": user_bearer_token,
                },
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        log.error("support_agent_call_failed", error=str(exc))
        return {"response_text": "The support agent is temporarily unavailable."}


async def rag_search(query: str, top_k: int = 3) -> list[dict]:
    url = f"{settings.rag_service_url}/search"
    try:
        async with httpx.AsyncClient(timeout=settings.downstream_timeout_seconds, headers=internal_headers()) as client:
            resp = await client.post(url, json={"query": query, "top_k": top_k})
            resp.raise_for_status()
            return resp.json().get("results", [])
    except Exception as exc:
        log.error("rag_search_failed", error=str(exc))
        return []


async def send_trace(payload: dict) -> None:
    """Fire-and-forget call to the Eval Service. Never raises."""
    url = f"{settings.eval_service_url}/traces"
    try:
        async with httpx.AsyncClient(timeout=settings.downstream_timeout_seconds, headers=internal_headers()) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
    except Exception as exc:
        log.error("trace_send_failed", error=str(exc))
