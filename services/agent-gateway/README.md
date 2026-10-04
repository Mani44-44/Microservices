# Agent Gateway (port 8007)

Entry point for all AI interactions in TicketFlow. Handles conversation
persistence, input/output safety guardrails, deterministic intent
classification, routing to sub-agents/RAG/canned replies, rate limiting,
and fire-and-forget tracing.

## Run

```bash
pip install -r requirements.txt
uvicorn app.main:app --port 8007
```

## Endpoints

- `POST /api/v1/agent/chat` — main chat endpoint (requires auth)
- `GET /api/v1/agent/conversations` — list the caller's conversations
- `GET /api/v1/agent/conversations/{id}` — full message history
- `DELETE /api/v1/agent/conversations/{id}` — end a conversation
- `POST /api/v1/agent/conversations/{id}/escalate` — escalate to a human
- `GET /api/v1/agent/health` — health check (no auth)

## Chat flow

1. Rate limit check (Redis, 20 msgs/hour/user) — 429 `RATE_LIMITED` if exceeded, no downstream calls made.
2. Guardrail input check (`guardrail-service:8011 /guardrails/check-input`) — blocked requests stop here.
3. Deterministic keyword-based intent classification (`app/intent.py`, no LLM in this project) — logged to `agent_decisions`.
4. Route: TRAVEL_PLANNING → `travel-planner-agent:8008 /plan`; BOOKING_INQUIRY/REFUND_REQUEST → `support-agent:8009 /handle` (forwards the caller's own bearer token so it can act on booking-service on their behalf); GENERAL_QA → `rag-service:8010 /search`; CHITCHAT/ESCALATION → canned replies.
5. Guardrail output check (`guardrail-service:8011 /guardrails/check-output`) — uses the (possibly redacted) text.
6. Fire-and-forget trace to `eval-service:8012 /traces` (failures logged, never break the response).
7. Persist USER + AGENT messages to Postgres.

Every downstream call degrades gracefully (falls back to a canned
"temporarily unavailable" message) if the target service is unreachable or
errors, so this service works standalone.

## Contracts with sibling services (built independently, verified to match)

- Travel Planner `POST /plan`: `{"conversation_id": str, "user_id": str, "message": str}` → `{"response_text": str, ...}`
- Support Agent `POST /handle`: `{"conversation_id": str, "user_id": str, "message": str, "intent": str, "user_bearer_token": str}` → `{"response_text": str, ...}`

## Tests

```bash
pytest
```

Unit tests mock downstream httpx calls. `tests/test_live_integration.py` and
part of `tests/test_redteam.py` call the real Guardrail/Travel-Planner/Support
services and skip gracefully if unreachable.
