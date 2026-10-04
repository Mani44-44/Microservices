"""
Deterministic (no-LLM) constraint extraction from a free-text travel message.

Regex/keyword heuristics only — see phase3_prompt.md's ConstraintExtractor
spec for the fields; this is the "no real LLM" simplification agreed for
this project.
"""
import re
from datetime import date, timedelta

KNOWN_CITIES = [
    "Paris",
    "Tokyo",
    "Rome",
    "New York",
    "Barcelona",
    "Bangkok",
    "London",
    "Sydney",
]

INTEREST_KEYWORDS = [
    "food",
    "culture",
    "adventure",
    "relaxation",
    "shopping",
    "nightlife",
    "nature",
    "history",
]

DIETARY_KEYWORDS = [
    "vegetarian",
    "vegan",
    "gluten-free",
    "halal",
    "kosher",
    "allergy",
]

REQUIRED_FIELDS = ["destination", "start_date", "end_date"]

_ISO_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_IN_N_DAYS_RE = re.compile(r"\bin (\d+) days?\b", re.IGNORECASE)
_NEXT_WEEK_RE = re.compile(r"\bnext week\b", re.IGNORECASE)
_TOMORROW_RE = re.compile(r"\btomorrow\b", re.IGNORECASE)
_FOR_N_DAYS_RE = re.compile(r"\bfor (\d+) days?\b", re.IGNORECASE)
_BUDGET_RE = re.compile(r"\$\s?(\d+)|budget of\s*\$?\s*(\d+)", re.IGNORECASE)
_TRAVELERS_RE = re.compile(
    r"(\d+)\s*(?:people|travelers|traveller|travellers|adults|adult)", re.IGNORECASE
)


class ConstraintExtractor:
    """Stateless heuristic extractor. Call extract() per message."""

    def extract(self, message: str, existing_constraints: dict | None = None) -> dict:
        existing = dict(existing_constraints or {})
        extracted = self._extract_new(message)

        merged = {
            "destination": None,
            "start_date": None,
            "end_date": None,
            "budget": None,
            "num_travelers": None,
            "interests": [],
            "dietary_restrictions": [],
        }
        merged.update(existing)
        for key, value in extracted.items():
            if key in ("interests", "dietary_restrictions"):
                combined = list(dict.fromkeys((existing.get(key) or []) + (value or [])))
                if combined:
                    merged[key] = combined
            elif value is not None:
                merged[key] = value

        missing = [f for f in REQUIRED_FIELDS if not merged.get(f)]
        merged["missing_required_fields"] = missing
        return merged

    def _extract_new(self, message: str) -> dict:
        result: dict = {
            "destination": self._extract_destination(message),
            "budget": self._extract_budget(message),
            "num_travelers": self._extract_travelers(message),
            "interests": self._extract_keywords(message, INTEREST_KEYWORDS),
            "dietary_restrictions": self._extract_keywords(message, DIETARY_KEYWORDS),
        }
        start_date, end_date = self._extract_dates(message)
        result["start_date"] = start_date
        result["end_date"] = end_date
        return result

    def _extract_destination(self, message: str) -> str | None:
        lowered = message.lower()
        for city in KNOWN_CITIES:
            if city.lower() in lowered:
                return city
        return None

    def _extract_budget(self, message: str) -> float | None:
        match = _BUDGET_RE.search(message)
        if not match:
            return None
        raw = match.group(1) or match.group(2)
        return float(raw)

    def _extract_travelers(self, message: str) -> int | None:
        match = _TRAVELERS_RE.search(message)
        return int(match.group(1)) if match else None

    def _extract_keywords(self, message: str, keywords: list[str]) -> list[str]:
        lowered = message.lower()
        return [kw for kw in keywords if kw in lowered]

    def _extract_dates(self, message: str) -> tuple[str | None, str | None]:
        iso_dates = _ISO_DATE_RE.findall(message)
        if len(iso_dates) >= 2:
            start, end = sorted(iso_dates[:2])
            return start, end
        if len(iso_dates) == 1:
            start = date.fromisoformat(iso_dates[0])
            return self._apply_duration(start, message)

        today = date.today()
        if _TOMORROW_RE.search(message):
            return self._apply_duration(today + timedelta(days=1), message)

        in_n_match = _IN_N_DAYS_RE.search(message)
        if in_n_match:
            start = today + timedelta(days=int(in_n_match.group(1)))
            return self._apply_duration(start, message)

        if _NEXT_WEEK_RE.search(message):
            start = today + timedelta(days=7)
            return self._apply_duration(start, message)

        return None, None

    def _apply_duration(self, start: date, message: str) -> tuple[str, str | None]:
        duration_match = _FOR_N_DAYS_RE.search(message)
        if duration_match:
            num_days = int(duration_match.group(1))
            end = start + timedelta(days=max(num_days - 1, 0))
            return start.isoformat(), end.isoformat()
        return start.isoformat(), None
