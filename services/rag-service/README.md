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

## Embeddings: lexical, no API key

`app/embeddings.py` builds a deterministic, feature-hashed bag-of-words
vector (lowercased words minus stopwords, light suffix stemming, `1 + log(tf)`
weights, signed hashing into 1536 dims, L2-normalized). Texts that share
words are close under cosine distance, so pgvector `<=>` search returns
lexically relevant chunks: 100% top-1 on the 20 labelled questions in
`tests/test_retrieval_quality.py` (the previous SHA-256-seeded random vectors
scored 10%).

A small domain synonym map (`_PHRASES` / `_CANONICAL`: "money back" ->
refund, concert/gig/show -> event, luggage -> pack, abroad -> international,
Schengen country names, ...) folds common paraphrases onto the docs' words.
On a 12-question held-out set written before that map existed, top-1 went
from 4/12 to 9/12 and top-3 from 8/12 to 12/12.

**Limitation:** it is keyword matching, not semantics — a question that
shares no word stem with the answer ("money back" vs "refund") won't find it.
Results scoring below `MIN_RELEVANCE_SCORE` (default 0.15) are dropped, so
such questions get no result (the agent says it doesn't know / escalates)
rather than an unrelated snippet. To go further, swap a real embedding model
in behind `embed_text()`; on startup `reembed_if_stale()` notices stored
vectors from a different embedder and re-embeds them.

If `GROQ_API_KEY` is set, `/search` additionally re-ranks the retrieved
top-K with an LLM (`openai/gpt-oss-20b`, via `libs/llm/groq_client.py`); on a
Groq error/timeout, or with no key, the embedding order is returned
unchanged (see `app/rag.py::rerank_chunks`).

## Tests

```
pytest
```

Unit tests (`test_embeddings.py`, `test_ingest.py`) run against in-memory
SQLite — no real DB needed. `test_pgvector_integration.py` requires a live
Postgres+pgvector connection and is skipped automatically if one isn't
reachable (vector cosine search has no SQLite equivalent to test against).
