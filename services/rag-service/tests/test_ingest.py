"""SQLite-backed: exercises ingest_document's chunking/embedding/persistence
logic. Vector similarity search itself needs real pgvector -- see
test_pgvector_integration.py for that."""
import uuid

from sqlalchemy import select

from app.models import Document, DocumentChunk
from app.rag import ingest_document


async def test_ingest_document_creates_document_and_chunks(db_session):
    doc_id, chunks_created = await ingest_document(
        db_session,
        content="a" * 1200,
        category="travel",
        source="unit-test",
        metadata={"foo": "bar"},
    )
    await db_session.flush()

    assert chunks_created == 3

    doc_uuid = uuid.UUID(doc_id)
    document = (
        await db_session.execute(select(Document).where(Document.id == doc_uuid))
    ).scalar_one_or_none()
    assert document is not None
    assert document.category == "travel"

    chunks = (
        await db_session.execute(
            select(DocumentChunk).where(DocumentChunk.document_id == doc_uuid)
        )
    ).scalars().all()
    assert len(chunks) == 3
    assert all(c.category == "travel" and c.source == "unit-test" for c in chunks)
    assert all(len(c.embedding) == 1536 for c in chunks)


async def test_ingest_document_short_content_single_chunk(db_session):
    doc_id, chunks_created = await ingest_document(
        db_session, content="Refunds within 7 days are full.", category="support", source="policy"
    )
    assert chunks_created == 1
