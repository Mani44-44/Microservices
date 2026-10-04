"""Unit tests for the real-Groq relevance re-ranking pass on top of the mock
retrieval. Uses FakeGroqClient exclusively -- the real GroqClient is never
hit here."""
from app.rag import rerank_chunks
from libs.llm.groq_client import FakeGroqClient, ToolCallResult

CANDIDATES = [
    {"chunk_id": "a", "content": "The cat sat on the mat.", "category": "x", "source": "s", "score": 0.9, "metadata": {}},
    {"chunk_id": "b", "content": "Refunds are processed within 7 days.", "category": "x", "source": "s", "score": 0.5, "metadata": {}},
    {"chunk_id": "c", "content": "Paris is the capital of France.", "category": "x", "source": "s", "score": 0.3, "metadata": {}},
]


async def test_rerank_reorders_by_llm_judgment():
    """Mock retrieval put "a" first (highest mock score), but the query is
    about refunds -- the LLM should be able to put "b" first instead, and the
    final result should reflect that real reordering."""
    fake = FakeGroqClient(
        tool_responses=[ToolCallResult(name="rerank_chunks", arguments={"ranked_chunk_ids": ["b", "c", "a"]})]
    )
    result = await rerank_chunks(fake, "refund policy", CANDIDATES, top_k=3)
    assert [c["chunk_id"] for c in result] == ["b", "c", "a"]
    assert len(fake.calls) == 1


async def test_rerank_truncates_to_top_k():
    fake = FakeGroqClient(
        tool_responses=[ToolCallResult(name="rerank_chunks", arguments={"ranked_chunk_ids": ["c", "b", "a"]})]
    )
    result = await rerank_chunks(fake, "france", CANDIDATES, top_k=2)
    assert [c["chunk_id"] for c in result] == ["c", "b"]


async def test_rerank_appends_ids_llm_omitted():
    fake = FakeGroqClient(
        tool_responses=[ToolCallResult(name="rerank_chunks", arguments={"ranked_chunk_ids": ["c"]})]
    )
    result = await rerank_chunks(fake, "france", CANDIDATES, top_k=3)
    assert [c["chunk_id"] for c in result] == ["c", "a", "b"]


async def test_rerank_falls_back_to_original_order_on_llm_error():
    """No tool_responses/default_tool configured -> FakeGroqClient raises
    LLMError -> original mock-embedding order must be preserved unchanged."""
    fake = FakeGroqClient()
    result = await rerank_chunks(fake, "refund policy", CANDIDATES, top_k=3)
    assert [c["chunk_id"] for c in result] == ["a", "b", "c"]


async def test_rerank_falls_back_on_invalid_ranking_shape():
    fake = FakeGroqClient(
        tool_responses=[ToolCallResult(name="rerank_chunks", arguments={"ranked_chunk_ids": "not-a-list"})]
    )
    result = await rerank_chunks(fake, "refund policy", CANDIDATES, top_k=3)
    assert [c["chunk_id"] for c in result] == ["a", "b", "c"]
