"""
FastAPI dependency for the Groq LLM client, following the same
Depends()/dependency_overrides pattern as app.database.get_db so tests can
swap in libs.llm.groq_client.FakeGroqClient without touching real network.
"""
from functools import lru_cache

from libs.llm.groq_client import GroqClient


@lru_cache
def get_llm_client() -> GroqClient:
    return GroqClient()
