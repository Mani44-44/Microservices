import pytest

from app.itinerary_builder import build_itinerary, fetch_rag_snippet
from libs.llm.groq_client import FakeGroqClient


@pytest.mark.asyncio
async def test_rag_call_degrades_gracefully_when_unreachable():
    # Port 8010 is not running during tests -> must return None, not raise.
    result = await fetch_rag_snippet("http://localhost:8010", "Paris")
    assert result is None


@pytest.mark.asyncio
async def test_build_itinerary_end_to_end_without_rag():
    items, days, response_text = await build_itinerary(
        destination="Paris",
        start_date="2026-09-10",
        end_date="2026-09-12",
        interests=["food"],
        dietary_restrictions=["vegetarian"],
        rag_service_url="http://localhost:8010",
        llm_client=FakeGroqClient(default_text="A lovely 3-day trip to Paris awaits, vegetarian-friendly."),
    )
    assert len(days) == 3  # 3 days inclusive
    assert len(items) == 3 * 5  # 5 slots/day
    assert "Paris" in response_text
    assert "vegetarian" in response_text.lower()


@pytest.mark.asyncio
async def test_build_itinerary_uses_llm_response_text_verbatim():
    """response_text must be exactly what the LLM returned, not re-templated."""
    fake = FakeGroqClient(complete_responses=["Bonjour! Day 1: croissants. Day 2: the Louvre."])
    items, days, response_text = await build_itinerary(
        destination="Paris",
        start_date="2026-09-10",
        end_date="2026-09-11",
        interests=None,
        dietary_restrictions=None,
        rag_service_url="http://localhost:8010",
        llm_client=fake,
    )
    assert response_text == "Bonjour! Day 1: croissants. Day 2: the Louvre."
    # The prompt sent to the LLM carries the real, mock-API-grounded day data.
    assert len(fake.calls) == 1
    assert "Paris" in fake.calls[0]["user"]
    assert len(days) == 2
    assert len(items) == 2 * 5
