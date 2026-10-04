# Evaluation & Tracing Service

Port **8012**. Records one trace row per end-to-end agent run and exposes read/aggregate stats over them.

## Scope note

The literal Phase 3 spec describes several tables (`agent_traces`, `tool_call_traces`,
`guardrail_traces`, `evaluation_metrics`) and per-conversation/per-message trace
endpoints, implying every sub-agent and the guardrail service write incrementally.
This build deliberately simplifies that: **only the Agent Gateway calls this
service**, once per completed agent run, with the full step/tool-call/guardrail
breakdown embedded in a single `POST /traces` body (`steps` JSON list). This
keeps the contract to one table (`agent_traces`) and one write path. Flagged
per project convention rather than silently narrowed.

## Endpoints

- `POST /traces` — ingest one trace for a completed agent run. `trace_id` is
  generated if omitted. Returns `{"trace_id": "..."}`.
- `GET /traces/{trace_id}` — full stored trace.
- `GET /traces?agent_type=&limit=50` — recent traces, newest first.
- `GET /stats` — aggregates: `total_traces`, `by_agent_type`, `avg_duration_ms`,
  `total_estimated_cost_usd`, `guardrail_block_rate`, `error_rate`.
- `GET /health` — `{"status": "ok"}`.

No auth — internal use only (called by the Agent Gateway), not exposed publicly.

## Simulated cost

There is no real LLM anywhere in this project (mock providers only, no API
keys). If a caller doesn't supply `estimated_cost_usd`, cost is computed as
`mock_llm_calls * cost_per_mock_call_usd` (default `$0.0002`, see
`app/config.py`). This is an illustrative number for the demo/dashboard, not
real spend.

## Running

```
uvicorn app.main:app --port 8012
```

Requires Postgres on `localhost:5433` (`ticketflow_eval` db, shared container
with the other services) — see `app/config.py` for connection settings via
`.env`. Tables are created automatically on startup (no Alembic).

## Tests

```
pytest
```

SQLite (aiosqlite, in-memory) backed — no real Postgres needed.
