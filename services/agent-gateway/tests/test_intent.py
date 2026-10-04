import pytest

from app.intent import classify, classify_llm
from libs.llm.groq_client import FakeGroqClient, LLMError, ToolCallResult

CASES = [
    ("Plan a trip to Paris for 5 days", "TRAVEL_PLANNING"),
    ("What to do in Tokyo?", "TRAVEL_PLANNING"),
    ("What's my booking status?", "BOOKING_INQUIRY"),
    ("I want to cancel my booking", "BOOKING_INQUIRY"),
    ("I want a refund", "REFUND_REQUEST"),
    ("How do I get my money back?", "REFUND_REQUEST"),
    ("What events are happening this weekend?", "GENERAL_QA"),
    ("How does booking work on this site?", "GENERAL_QA"),
    ("Hello!", "CHITCHAT"),
    ("Thank you so much", "CHITCHAT"),
    ("I want to talk to a human", "ESCALATION"),
    ("Get me a representative", "ESCALATION"),
]


@pytest.mark.parametrize("message,expected", CASES)
def test_classify_intent(message, expected):
    result = classify(message)
    assert result["intent"] == expected
    assert 0.0 <= result["confidence"] <= 1.0
    assert isinstance(result["reasoning"], str)


def test_refund_wins_over_travel_wording():
    # "refund" should win priority over travel-sounding words per our
    # documented priority order (escalation > refund > booking > travel > chitchat).
    result = classify("I want a refund for my trip to Paris")
    assert result["intent"] == "REFUND_REQUEST"


@pytest.mark.asyncio
async def test_classify_llm_uses_structured_tool_response():
    """The tool-use path: FakeGroqClient hands back a ToolCallResult and
    classify_llm() must use it as-is, never falling back to regex."""
    fake = FakeGroqClient(
        default_tool=ToolCallResult(
            name="classify_intent",
            arguments={"intent": "REFUND_REQUEST", "confidence": 0.95, "reasoning": "user asked for money back"},
        )
    )
    result = await classify_llm(fake, "give me my money back please")
    assert result == {
        "intent": "REFUND_REQUEST",
        "confidence": 0.95,
        "reasoning": "user asked for money back",
    }
    assert len(fake.calls) == 1
    assert fake.calls[0]["tool_name"] == "classify_intent"


@pytest.mark.asyncio
async def test_classify_llm_falls_back_to_regex_on_llm_error():
    """If the Groq call raises LLMError (timeout/outage/bad JSON), classify_llm
    must degrade to the deterministic regex classifier, not raise."""

    class _AlwaysFails(FakeGroqClient):
        async def complete_with_tool(self, *args, **kwargs):
            raise LLMError("simulated Groq timeout")

    result = await classify_llm(_AlwaysFails(), "I want a refund")
    assert result["intent"] == "REFUND_REQUEST"  # regex fallback still gets it right
    assert result == classify("I want a refund")


@pytest.mark.asyncio
async def test_classify_llm_falls_back_on_invalid_intent_from_model():
    """A model hallucinating an intent outside our taxonomy must also fall
    back to regex rather than propagating garbage."""
    fake = FakeGroqClient(
        default_tool=ToolCallResult(
            name="classify_intent",
            arguments={"intent": "NOT_A_REAL_INTENT", "confidence": 0.9, "reasoning": "oops"},
        )
    )
    result = await classify_llm(fake, "Plan a trip to Paris")
    assert result["intent"] == "TRAVEL_PLANNING"  # regex fallback
