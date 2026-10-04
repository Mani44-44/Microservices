# Travel Planner Agent

Port 8008. Multi-turn constraint gathering + template-based itinerary
generation for TicketFlow. No real LLM (no API keys in this project) —
`ConstraintExtractor` and itinerary generation are deterministic
regex/heuristic/template based.

## Run

```
pip install -r requirements.txt
uvicorn app.main:app --port 8008
```

## Contract

`POST /plan` — body `{"conversation_id", "user_id", "message"}`.

- If required fields (destination, start_date, end_date) are still missing
  after merging the message's extracted constraints into the stored ones:
  `{"response_text", "status": "GATHERING", "constraints", "missing_fields"}`.
- Once complete: `{"response_text", "status": "COMPLETE", "constraints",
  "itinerary_id", "itinerary": {"destination", "days": [...]}}`. Persists
  `itinerary_items` rows.

`GET /health` -> `{"status": "ok"}`.

`GET /mock/weather|places|events|restaurants` — the mock external APIs used
internally by the itinerary builder, also exposed as real HTTP routes.

## Simplifications vs. the original spec

- No `destination_knowledge` or `external_api_cache` tables — destination
  content comes from the RAG Service over HTTP (`rag_service_url`,
  default `http://localhost:8010`); mock APIs are instant so caching adds
  nothing.
- RAG call is best-effort: if the RAG service is unreachable, the plan
  still completes without the extra citation snippet.
