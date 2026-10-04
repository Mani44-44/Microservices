"""
checks.py
~~~~~~~~~
Deterministic, regex/keyword-based guardrail heuristics. No LLM calls -
this project has no LLM API keys, so "detection" here means pattern
matching, not semantic understanding. Good enough to catch the literal
red-team strings in phase3_prompt.md; a real deployment would layer an
LLM or a trained classifier on top of this.
"""
import re

# --- Prompt injection ---------------------------------------------------
# (pattern, weight, attack_type). Weights are heuristic; scores are summed
# and capped at 1.0. Multiple weak signals stacking up to a block is
# intentional (e.g. "developer" + "bypass" together is more suspicious
# than either alone).
INJECTION_PATTERNS: list[tuple[str, float, str]] = [
    (r"ignore (all )?(the )?previous instructions", 0.9, "instruction_override"),
    (r"disregard (all|the above)", 0.9, "instruction_override"),
    (r"you are now", 0.75, "role_override"),
    (r"pretend (you are|to be|you don'?t have)", 0.75, "jailbreak_roleplay"),
    (r"\bDAN\b", 0.5, "jailbreak_roleplay"),
    (r"jailbreak", 0.7, "jailbreak_roleplay"),
    (r"developer mode", 0.6, "jailbreak_roleplay"),
    (r"system prompt", 0.5, "prompt_extraction"),
    (r"reveal your (instructions|prompt)", 0.7, "prompt_extraction"),
    (r"what (was|is) your (system )?prompt", 0.6, "prompt_extraction"),
    (r"\boverride\b", 0.4, "override_attempt"),
    (r"\bbypass\b", 0.5, "override_attempt"),
    (r"grant (admin|root) access", 0.7, "privilege_escalation"),
    (r"base64", 0.4, "encoding_trick"),
    (r"execute this command", 0.75, "command_injection"),
    (r"as a developer,? i need you to", 0.4, "social_engineering"),
    # Requests for another user's PII out of internal systems/DBs - not
    # injection in the classic sense, but the same "get the model to leak
    # sensitive data it shouldn't" family the spec asks us to catch, so we
    # score them here rather than inventing a third detector.
    (r"(credit card|phone number|address|email) (of|for|from) (the )?(user|database|last)", 0.7, "pii_exfiltration_attempt"),
]

# Base64-looking blob (long run of base64 alphabet chars) is a weaker,
# structural signal on top of the literal word "base64" above.
_BASE64_BLOB_RE = re.compile(r"\b[A-Za-z0-9+/]{24,}={0,2}\b")

_COMPILED_INJECTION = [(re.compile(p, re.IGNORECASE), w, t) for p, w, t in INJECTION_PATTERNS]


def detect_injection(text: str) -> dict:
    matched = []
    score = 0.0
    for regex, weight, attack_type in _COMPILED_INJECTION:
        if regex.search(text):
            matched.append(attack_type)
            score += weight
    if _BASE64_BLOB_RE.search(text):
        matched.append("encoding_trick")
        score += 0.3
    score = min(score, 1.0)
    return {
        "is_injection": score > 0,
        "risk_score": round(score, 2),
        "matched_patterns": matched,
    }


# --- PII detection / redaction ------------------------------------------
PII_PATTERNS: dict[str, re.Pattern] = {
    "EMAIL": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "PHONE": re.compile(r"\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b"),
    "SSN": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    # 13-19 digit sequences, optionally grouped with dashes/spaces.
    "CREDIT_CARD": re.compile(r"\b(?:\d[ -]?){12,18}\d\b"),
}
# Order matters: SSN and CREDIT_CARD patterns can overlap with PHONE-length
# digit runs, so check the more specific ones first when redacting.
_REDACT_ORDER = ["EMAIL", "SSN", "CREDIT_CARD", "PHONE"]


def detect_pii(text: str) -> list[str]:
    """Returns the list of PII types found (e.g. ["EMAIL", "PHONE"])."""
    found = []
    remaining = text
    for pii_type in _REDACT_ORDER:
        matches = PII_PATTERNS[pii_type].findall(remaining)
        if matches:
            found.append(pii_type)
            # Remove matched spans so a credit card isn't double-counted as a phone number.
            remaining = PII_PATTERNS[pii_type].sub(" ", remaining)
    return found


def redact(text: str) -> tuple[str, list[str]]:
    """Replace PII with [REDACTED_<TYPE>] placeholders. Returns (redacted_text, findings)."""
    redacted = text
    found = []
    for pii_type in _REDACT_ORDER:
        if PII_PATTERNS[pii_type].search(redacted):
            found.append(pii_type)
            redacted = PII_PATTERNS[pii_type].sub(f"[REDACTED_{pii_type}]", redacted)
    return redacted, found


# --- Hallucination heuristic ---------------------------------------------
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?%?")
_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "to", "of", "and", "or",
    "in", "on", "for", "with", "this", "that", "it", "your", "you", "will",
    "be", "can", "if", "not", "as", "at", "by", "from",
}


def check_hallucination(text: str, context_chunks: list[str]) -> dict:
    """Heuristic, NOT a real hallucination detector.

    Flags an output when it asserts a specific number (a policy percentage,
    a dollar amount, a day count, etc.) that does not appear verbatim in any
    context chunk. This catches the common "agent invents a refund
    percentage / deadline" failure mode cheaply, but it does not verify
    prose claims, entity relationships, or anything without a literal
    number attached - a made-up qualitative claim ("refunds are always
    instant") will not be caught. A real system needs NLI-based claim
    verification or an LLM judge; this is a documented, deliberate
    simplification given no LLM is available here.
    """
    if not context_chunks:
        numbers = _NUMBER_RE.findall(text)
        return {
            "hallucination_flagged": bool(numbers),
            "unsupported_claims": numbers,
        }

    context_blob = " ".join(context_chunks)
    output_numbers = _NUMBER_RE.findall(text)
    unsupported = [n for n in output_numbers if n not in context_blob]

    return {
        "hallucination_flagged": len(unsupported) > 0,
        "unsupported_claims": unsupported,
    }
