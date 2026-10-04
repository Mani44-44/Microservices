"""
routing.py
~~~~~~~~~~
Simple keyword/regex routing used by POST /handle. No LLM — a UUID regex
finds a booking_id if one appears in the message, and a small keyword list
distinguishes a general policy question from one about a specific booking.
"""
import re

_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)

_AMOUNT_RE = re.compile(r"\$\s*(\d+(?:\.\d{1,2})?)")


def extract_booking_id(message: str) -> str | None:
    match = _UUID_RE.search(message)
    return match.group(0) if match else None


def extract_amount(message: str) -> float | None:
    match = _AMOUNT_RE.search(message)
    return float(match.group(1)) if match else None
