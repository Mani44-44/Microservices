# RAG Retrieval Service

Vector search over travel-planning and support-policy knowledge for TicketFlow's
Phase 3 agents (Travel Planner Agent, Support Agent). Runs on port 8010.

## Run

```
pip install -r requirements.txt
uvicorn app.main:app --port 8010
```

Needs a Postgres instance with the `pgvector` extension on port 5434 (see
`app/config.py`). On startup the service creates its tables and, if the
`documents` table is empty, seeds ~20 short travel/support documents from
`app/seed_data.py`.

## Endpoints

- `POST /documents` — `{"content", "category", "source", "metadata"?}` → chunks
  (~500 chars, 50-char overlap), embeds each chunk, stores it. Returns
  `{"document_id", "chunks_created"}`.
- `POST /search` — `{"query", "category"?, "top_k"?=5}` → cosine-distance
  nearest-neighbor search (pgvector `<=>`), optionally filtered by category.
  Returns `{"results": [{"chunk_id", "content", "category", "source", "score",
  "metadata"}, ...]}`.
- `GET /health` — `{"status": "ok"}`.

No auth on any endpoint — internal service-to-service calls only.

## Known limitation: mock embeddings

There are no LLM/embedding API keys in this project. `app/embeddings.py`
hashes each chunk's text (SHA-256) and uses it to seed a PRNG that produces a
deterministic, L2-normalized 1536-dim vector. Same text always embeds to the
same vector, which keeps ingestion/search testable, but **similar text does
not embed to similar vectors** — it's a hash, not a real embedding, so
retrieval order out of `search_chunks` on its own is not meaningful.

To compensate, `/search` runs a real Groq LLM (`openai/gpt-oss-20b`, via
`libs/llm/groq_client.py`) as a **relevance re-ranking pass on top of** the
mock-retrieved top-K — genuine LLM judgment reorders the candidates the mock
step already picked, it does not replace mock retrieval itself. On a Groq
error/timeout the original mock order is returned unchanged (see
`app/rag.py::rerank_chunks`).

## Tests

```
pytest
```

Unit tests (`test_embeddings.py`, `test_ingest.py`) run against in-memory
SQLite — no real DB needed. `test_pgvector_integration.py` requires a live
Postgres+pgvector connection and is skipped automatically if one isn't
reachable (vector cosine search has no SQLite equivalent to test against).
