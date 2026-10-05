import pytest

from libs.llm.groq_client import GroqClient, LLMError


@pytest.mark.asyncio
async def test_missing_api_key_degrades_to_llm_error_per_call(monkeypatch):
    """No key must not crash service startup; each call raises LLMError so
    every caller's deterministic fallback path takes over."""
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    client = GroqClient()

    with pytest.raises(LLMError):
        await client.complete(system="s", user="u")
    with pytest.raises(LLMError):
        await client.complete_with_tool(
            system="s", user="u", tool_name="t", tool_description="d", parameters_schema={}
        )
