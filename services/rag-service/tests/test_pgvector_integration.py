"""Requires a real Postgres+pgvector connection (DATABASE_URL). Skipped
gracefully when unreachable -- vector cosine search (`<=>`) has no SQLite
equivalent, so it can't be covered by the unit tests above."""
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.config import get_settings
from app.database import Base
from app.rag import ingest_document, search_chunks
from libs.llm.groq_client import FakeGroqClient, ToolCallResult

settings = get_settings()


def _test_database_url(database: str) -> str:
    return (
        f"postgresql+asyncpg://{settings.postgres_user}:{settings.postgres_password}"
        f"@{settings.postgres_host}:{settings.postgres_port}/{database}"
    )


async def _ensure_test_database() -> str:
    """These tests commit rows, so they always run in a dedicated `<db>_test`
    database (created on demand), never the service's own knowledge base."""
    name = settings.postgres_db if settings.postgres_db.endswith("_test") else f"{settings.postgres_db}_test"
    admin = create_async_engine(_test_database_url("postgres"), isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            exists = (
                await conn.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name})
            ).scalar()
            if not exists:
                await conn.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        await admin.dispose()
    return name


@pytest_asyncio.fixture()
async def pg_session():
    try:
        database = await _ensure_test_database()
    except Exception:
        pytest.skip("No live Postgres+pgvector connection available")
    engine = create_async_engine(_test_database_url(database))

    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
        await session.rollback()
    await engine.dispose()


async def test_search_returns_results_and_respects_category(pg_session):
    await ingest_document(
        pg_session, content="Refunds are full within 7 days.", category="support", source="policy"
    )
    await ingest_document(
        pg_session, content="Paris is a popular travel destination.", category="travel", source="guide"
    )
    await pg_session.commit()

    results = await search_chunks(pg_session, query="refund policy", category="support", top_k=5)
    assert len(results) >= 1
    assert all(r["category"] == "support" for r in results)


async def test_search_reranks_with_injected_llm_client(pg_session):
    """llm_client is dependency-injected -- passing a FakeGroqClient must
    reorder results per its queued tool response and must never touch the
    real network."""
    doc_id, _ = await ingest_document(
        pg_session, content="Refunds are full within 7 days.", category="support", source="policy"
    )
    await pg_session.commit()

    mock_order = await search_chunks(pg_session, query="refund policy", category="support", top_k=5)
    chunk_id = mock_order[0]["chunk_id"]

    fake = FakeGroqClient(
        tool_responses=[ToolCallResult(name="rerank_chunks", arguments={"ranked_chunk_ids": [chunk_id]})]
    )
    reranked = await search_chunks(
        pg_session, query="refund policy", category="support", top_k=5, llm_client=fake
    )
    assert reranked[0]["chunk_id"] == chunk_id
    assert len(fake.calls) == 1


async def test_search_falls_back_to_mock_order_on_llm_error(pg_session):
    """FakeGroqClient with no queued response raises LLMError internally --
    search_chunks must fall back to the original mock-embedding order rather
    than failing the request."""
    await ingest_document(
        pg_session, content="Refunds are full within 7 days.", category="support", source="policy"
    )
    await pg_session.commit()

    mock_order = await search_chunks(pg_session, query="refund policy", category="support", top_k=5)
    fallback = await search_chunks(
        pg_session, query="refund policy", category="support", top_k=5, llm_client=FakeGroqClient()
    )
    assert [r["chunk_id"] for r in fallback] == [r["chunk_id"] for r in mock_order]


async def test_search_returns_nothing_for_unrelated_query(pg_session):
    await ingest_document(
        pg_session, content="Refunds are full within 7 days.", category="support", source="policy"
    )
    await pg_session.commit()

    assert await search_chunks(pg_session, query="what is the wifi password", category="support") == []
    hits = await search_chunks(pg_session, query="are refunds full within 7 days", category="support")
    assert hits and hits[0]["score"] >= settings.min_relevance_score


async def test_reembed_if_stale_rewrites_vectors_from_an_older_embedder(pg_session):
    from sqlalchemy import select

    from app.embeddings import _fallback_vector, embed_text
    from app.models import DocumentChunk
    from app.rag import reembed_if_stale

    await ingest_document(pg_session, content="Transfers are free.", category="support", source="t")
    await pg_session.commit()
    # Simulate vectors written by the previous (hash-seeded) embedder.
    for chunk in (await pg_session.execute(select(DocumentChunk))).scalars():
        chunk.embedding = _fallback_vector(chunk.content)
    await pg_session.commit()

    assert await reembed_if_stale(pg_session) >= 1
    await pg_session.commit()
    chunk = (await pg_session.execute(select(DocumentChunk).limit(1))).scalar_one()
    assert max(abs(a - b) for a, b in zip(chunk.embedding, embed_text(chunk.content))) < 1e-6
    assert await reembed_if_stale(pg_session) == 0


async def test_integration_tests_never_touch_the_service_database(pg_session):
    """These tests commit rows; pointed at the live DB they once leaked test
    documents into the production knowledge base."""
    current = (await pg_session.execute(text("SELECT current_database()"))).scalar_one()
    assert current.endswith("_test"), current


async def test_reembed_if_stale_catches_stale_chunks_even_when_some_are_fresh(pg_session):
    """An embedder change only alters chunks containing affected words, so
    checking a single sample chunk can wrongly conclude nothing is stale."""
    from sqlalchemy import select

    from app.embeddings import _fallback_vector, embed_text
    from app.models import DocumentChunk
    from app.rag import reembed_if_stale

    # Start from an empty (test-only) table so the sampled chunk is one of ours.
    await pg_session.execute(text("DELETE FROM document_chunks"))
    await pg_session.execute(text("DELETE FROM documents"))
    await pg_session.commit()
    for content in ("Seats can be moved.", "Gigs sell out.", "Luggage tips."):
        await ingest_document(pg_session, content=content, category="support", source="t")
    await pg_session.commit()
    sample = (await pg_session.execute(select(DocumentChunk).limit(1))).scalar_one()
    for chunk in (await pg_session.execute(select(DocumentChunk))).scalars():
        if chunk.id != sample.id:
            chunk.embedding = _fallback_vector(chunk.content)  # stale
    await pg_session.commit()

    await reembed_if_stale(pg_session)
    await pg_session.commit()

    for chunk in (await pg_session.execute(select(DocumentChunk))).scalars():
        assert max(abs(a - b) for a, b in zip(chunk.embedding, embed_text(chunk.content))) < 1e-6, chunk.content
