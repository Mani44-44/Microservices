from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.embeddings import chunk_text, embed_text
from app.models import Document, DocumentChunk
from libs.llm.groq_client import LLMError

log = structlog.get_logger()

_RERANK_SYSTEM_PROMPT = (
    "You are a relevance re-ranking assistant for a RAG search system. You will be given "
    "a user query and a list of candidate text chunks retrieved by a mock/random embedding "
    "similarity search -- their existing order and scores are NOT semantically meaningful. "
    "Read each candidate and use the rerank_chunks tool to return the chunk_id values ordered "
    "from most to least relevant to the query. Include every chunk_id exactly once."
)

_RERANK_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "ranked_chunk_ids": {
            "type": "array",
            "items": {"type": "string"},
            "description": "chunk_id values from the candidates, most relevant first.",
        }
    },
    "required": ["ranked_chunk_ids"],
}


async def ingest_document(
    db: AsyncSession, content: str, category: str, source: str, metadata: dict | None = None
) -> tuple[str, int]:
    """Chunk + embed a document and persist it. Used directly by both the
    POST /documents route and scripts/seed_rag.py (no HTTP round-trip needed
    for seeding)."""
    metadata = metadata or {}
    document = Document(content=content, category=category, source=source, doc_metadata=metadata)
    db.add(document)
    await db.flush()  # assigns document.id

    chunks = chunk_text(content)
    for index, chunk in enumerate(chunks):
        db.add(
            DocumentChunk(
                document_id=document.id,
                chunk_index=index,
                content=chunk,
                category=category,
                source=source,
                doc_metadata=metadata,
                embedding=embed_text(chunk),
            )
        )

    return str(document.id), len(chunks)


async def search_chunks(
    db: AsyncSession,
    query: str,
    category: str | None = None,
    top_k: int = 5,
    llm_client: Any | None = None,
) -> list[dict]:
    """Cosine-distance nearest-neighbor search over document_chunks (pgvector
    `<=>` operator), followed by an optional real-LLM relevance re-rank of the
    mock-retrieved top-K. Requires a real Postgres+pgvector backend.

    `llm_client` is dependency-injected (real GroqClient in production,
    FakeGroqClient in tests). If None, or if the rerank call fails, the
    original mock-embedding order is returned unchanged."""
    query_embedding = embed_text(query)
    distance = DocumentChunk.embedding.cosine_distance(query_embedding)

    stmt = select(DocumentChunk, distance.label("distance"))
    if category:
        stmt = stmt.where(DocumentChunk.category == category)
    stmt = stmt.order_by(distance).limit(top_k)

    rows = (await db.execute(stmt)).all()
    results = [
        {
            "chunk_id": str(chunk.id),
            "content": chunk.content,
            "category": chunk.category,
            "source": chunk.source,
            "score": 1.0 - distance_val,
            "metadata": chunk.doc_metadata,
        }
        for chunk, distance_val in rows
    ]

    if llm_client is None or not results:
        return results
    return await rerank_chunks(llm_client, query, results, top_k)


async def rerank_chunks(llm_client: Any, query: str, candidates: list[dict], top_k: int) -> list[dict]:
    """Re-rank mock-retrieved candidates by real Groq-judged relevance.

    Falls back to the original candidate order (truncated to top_k) on any
    LLMError -- an LLM hiccup must never fail the search request."""
    candidates_text = "\n".join(f"{c['chunk_id']}: {c['content']}" for c in candidates)
    user = f"Query: {query}\n\nCandidates:\n{candidates_text}"
    try:
        result = await llm_client.complete_with_tool(
            system=_RERANK_SYSTEM_PROMPT,
            user=user,
            tool_name="rerank_chunks",
            tool_description="Record the relevance-ranked order of candidate chunk ids.",
            parameters_schema=_RERANK_TOOL_SCHEMA,
            max_tokens=400,
        )
        ranked_ids = result.arguments.get("ranked_chunk_ids")
        if not isinstance(ranked_ids, list) or not ranked_ids:
            raise LLMError(f"Groq returned an invalid ranking: {result.arguments}")

        by_id = {c["chunk_id"]: c for c in candidates}
        reordered = [by_id[cid] for cid in ranked_ids if cid in by_id]
        seen = {c["chunk_id"] for c in reordered}
        reordered += [c for c in candidates if c["chunk_id"] not in seen]
        return reordered[:top_k]
    except LLMError as exc:
        log.warning("rerank_failed_fallback_to_mock_order", error=str(exc))
        return candidates[:top_k]
