"""
intent.py
~~~~~~~~~
Intent classification for the agent gateway.

`classify_llm()` is the real path: a Groq tool-use call forced into the
`classify_intent` tool so the model returns structured JSON, not prose to
regex against. `classify()` is the original deterministic keyword/regex
heuristic — kept as the fallback when the LLM call fails (timeout, API
error, malformed args), not as the primary path anymore.

ponytail: naive keyword heuristic, ceiling is ambiguous/compound messages
("I want a refund but also plan me a trip" -> only REFUND_REQUEST fires).
Same ceiling applies to the LLM path in principle, just less often.
"""
import re

import structlog

from libs.llm.groq_client import LLMError

log = structlog.get_logger()

INTENTS = [
    "TRAVEL_PLANNING",
    "BOOKING_INQUIRY",
    "REFUND_REQUEST",
    "GENERAL_QA",
    "CHITCHAT",
    "ESCALATION",
]

_ESCALATION_PATTERNS = [
    r"\bhuman\b", r"\bagent\b", r"\brepresentative\b", r"\breal person\b", r"\bspeak to someone\b",
]
_REFUND_PATTERNS = [
    r"\brefund\b", r"\bmoney back\b", r"\breimburse", r"\bcharge ?back\b",
]
_BOOKING_PATTERNS = [
    r"\bmy booking\b", r"\bbooking status\b", r"\bcancel my\b", r"\bmodify my booking\b",
    r"\breschedule\b", r"\bbooking id\b",
]
_TRAVEL_PATTERNS = [
    r"\bplan a trip\b", r"\bitinerary\b", r"\btravel to\b", r"\bvisit\b", r"\bthings to do\b",
    r"\bwhat to do in\b", r"\bvacation\b", r"\btrip to\b",
]
_CHITCHAT_PATTERNS = [
    r"\bhello\b", r"\bhi\b", r"\bhey\b", r"\bthanks\b", r"\bthank you\b", r"\bgood morning\b",
    r"\bgood evening\b",
]


def _matches(patterns: list[str], text_lower: str) -> list[str]:
    return [p for p in patterns if re.search(p, text_lower)]


def classify(message: str) -> dict:
    """Classify a message into one of INTENTS.

    Returns {"intent": str, "confidence": float, "reasoning": str}.
    """
    text_lower = message.lower()

    # Priority order matters: ESCALATION and REFUND are explicit asks that
    # should win over broader categories (e.g. "refund my trip booking").
    checks = [
        ("ESCALATION", _ESCALATION_PATTERNS),
        ("REFUND_REQUEST", _REFUND_PATTERNS),
        ("BOOKING_INQUIRY", _BOOKING_PATTERNS),
        ("TRAVEL_PLANNING", _TRAVEL_PATTERNS),
        ("CHITCHAT", _CHITCHAT_PATTERNS),
    ]
    for intent, patterns in checks:
        matched = _matches(patterns, text_lower)
        if matched:
            return {
                "intent": intent,
                "confidence": 0.9,
                "reasoning": f"Matched keyword pattern(s): {matched}",
            }

    return {
        "intent": "GENERAL_QA",
        "confidence": 0.5,
        "reasoning": "No specific intent keywords matched; defaulting to general Q&A.",
    }


_SYSTEM_PROMPT = (
    "You are an intent classifier for TicketFlow, a ticket-booking platform's chat agent gateway. "
    "Classify the user's message into exactly one intent using the classify_intent tool. Intents: "
    "TRAVEL_PLANNING (planning a trip/itinerary/vacation), "
    "BOOKING_INQUIRY (status/cancel/modify/reschedule an existing booking), "
    "REFUND_REQUEST (wants money back/refund/reimbursement/chargeback), "
    "GENERAL_QA (a factual question about the platform/events), "
    "CHITCHAT (greetings/thanks/small talk), "
    "ESCALATION (explicitly asks for a human/representative/real person). "
    "If a message could fit more than one, prefer ESCALATION, then REFUND_REQUEST, "
    "then BOOKING_INQUIRY, then TRAVEL_PLANNING, then CHITCHAT, then GENERAL_QA."
)

_TOOL_PARAMETERS_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": INTENTS},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "reasoning": {"type": "string", "description": "One short sentence explaining the classification."},
    },
    "required": ["intent", "confidence", "reasoning"],
}


async def classify_llm(client, message: str) -> dict:
    """Classify via a real Groq tool-use call (structured JSON, not prompt+regex).

    Falls back to the regex heuristic `classify()` on any `LLMError` -- a
    Groq timeout/outage/malformed response should degrade gracefully, not
    take the whole gateway down.
    """
    try:
        result = await client.complete_with_tool(
            system=_SYSTEM_PROMPT,
            user=message,
            tool_name="classify_intent",
            tool_description="Record the classified intent for a user chat message.",
            parameters_schema=_TOOL_PARAMETERS_SCHEMA,
            max_tokens=300,
        )
        intent = result.arguments.get("intent")
        confidence = result.arguments.get("confidence")
        if intent not in INTENTS or not isinstance(confidence, (int, float)):
            raise LLMError(f"LLM returned an invalid classification: {result.arguments}")
        return {
            "intent": intent,
            "confidence": float(confidence),
            "reasoning": result.arguments.get("reasoning", ""),
        }
    except LLMError as exc:
        log.warning("llm_classification_failed_fallback_to_regex", error=str(exc))
        return classify(message)
