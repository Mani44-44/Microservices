"""
Thin wrapper around Groq's free, OpenAI-compatible chat-completions API.

No `groq`/`openai` SDK dependency -- every service already ships `httpx`,
and Groq's REST shape is a plain OpenAI-compatible POST, so a dedicated SDK
would just be one more thing to pin and vendor.

`openai/gpt-oss-20b` is a reasoning model: it burns hidden "reasoning" tokens
before it writes `content`, so callers must budget `max_tokens` generously
(the reasoning tokens count against it) -- see `complete()`'s default.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog

log = structlog.get_logger()

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "openai/gpt-oss-20b"


class LLMError(Exception):
    """Raised when the Groq call fails or returns no usable content."""


@dataclass
class ToolCallResult:
    """Structured output from a forced tool-use call."""

    name: str
    arguments: dict[str, Any]


class GroqClient:
    """Real client -- calls the live Groq API. Never used in tests."""

    def __init__(self, api_key: str | None = None, model: str = DEFAULT_MODEL):
        self.api_key = api_key or os.environ.get("GROQ_API_KEY", "")
        self.model = model
        if not self.api_key:
            raise LLMError("GROQ_API_KEY not set")

    async def complete(
        self,
        system: str,
        user: str,
        max_tokens: int = 512,
        temperature: float = 0.3,
    ) -> str:
        """Plain free-text completion. Returns the assistant's text content."""
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        data = await self._post(body)
        content = data["choices"][0]["message"].get("content", "")
        if not content:
            raise LLMError(f"Groq returned empty content: {data}")
        return content

    async def complete_with_tool(
        self,
        system: str,
        user: str,
        tool_name: str,
        tool_description: str,
        parameters_schema: dict[str, Any],
        max_tokens: int = 512,
    ) -> ToolCallResult:
        """
        Structured-output completion via forced tool-use (not prompt+regex).
        Groq's tool-calling is OpenAI-compatible: `tool_choice` pinned to the
        one tool we offer forces the model to respond with valid JSON args.
        """
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.0,
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "description": tool_description,
                        "parameters": parameters_schema,
                    },
                }
            ],
            "tool_choice": {"type": "function", "function": {"name": tool_name}},
        }
        data = await self._post(body)
        message = data["choices"][0]["message"]
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            raise LLMError(f"Groq returned no tool call: {data}")
        call = tool_calls[0]["function"]
        try:
            arguments = json.loads(call["arguments"])
        except json.JSONDecodeError as e:
            raise LLMError(f"Groq tool call args not valid JSON: {call['arguments']!r}") from e
        return ToolCallResult(name=call["name"], arguments=arguments)

    async def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(GROQ_API_URL, json=body, headers=headers)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            raise LLMError(f"Groq API error {e.response.status_code}: {e.response.text}") from e
        except httpx.HTTPError as e:
            raise LLMError(f"Groq API request failed: {e}") from e


@dataclass
class FakeGroqClient:
    """
    Deterministic stand-in for tests -- never makes network calls.

    `complete_responses` / `tool_responses` are consumed in call order (FIFO);
    if exhausted, `default_text` / `default_tool` are used instead so a test
    that doesn't care about exact wording still gets a stable response.
    """

    default_text: str = "OK"
    default_tool: ToolCallResult | None = None
    complete_responses: list[str] = field(default_factory=list)
    tool_responses: list[ToolCallResult] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def complete(
        self,
        system: str,
        user: str,
        max_tokens: int = 512,
        temperature: float = 0.3,
    ) -> str:
        self.calls.append({"system": system, "user": user, "max_tokens": max_tokens})
        if self.complete_responses:
            return self.complete_responses.pop(0)
        return self.default_text

    async def complete_with_tool(
        self,
        system: str,
        user: str,
        tool_name: str,
        tool_description: str,
        parameters_schema: dict[str, Any],
        max_tokens: int = 512,
    ) -> ToolCallResult:
        self.calls.append({"system": system, "user": user, "tool_name": tool_name})
        if self.tool_responses:
            return self.tool_responses.pop(0)
        if self.default_tool is not None:
            return self.default_tool
        raise LLMError("FakeGroqClient: no tool_responses/default_tool configured")
