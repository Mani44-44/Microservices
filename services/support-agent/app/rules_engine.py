"""
rules_engine.py
~~~~~~~~~~~~~~~
Deterministic refund-eligibility rules. No LLM, no guessing — this is the
one function the whole "support agent never gives wrong refund info"
guarantee rests on, so keep it pure and easy to hand-trace.
"""
from datetime import datetime, timedelta
from typing import TypedDict


class EligibilityResult(TypedDict):
    refund_percentage: int
    reason: str
    rule: str


class RefundRulesEngine:
    """
    RULES (evaluated in this order):
      1. event cancelled by organizer          -> 100%
      2. event starts within 24 hours (or has already started/passed)
                                                 -> 0%
      3. event starts within 7 days             -> 50%
      4. event starts more than 7 days out      -> 100%
    """

    WITHIN_24H = timedelta(hours=24)
    WITHIN_7D = timedelta(days=7)

    def check_eligibility(
        self,
        event_date: datetime,
        event_status: str,
        current_date: datetime,
    ) -> EligibilityResult:
        if event_status == "CANCELLED":
            return {
                "refund_percentage": 100,
                "reason": "Event cancellation",
                "rule": "event_cancelled",
            }

        time_until_event = event_date - current_date

        if time_until_event <= self.WITHIN_24H:
            return {
                "refund_percentage": 0,
                "reason": "Too close to event date",
                "rule": "within_24h",
            }

        if time_until_event <= self.WITHIN_7D:
            return {
                "refund_percentage": 50,
                "reason": "Partial refund window",
                "rule": "within_7d",
            }

        return {
            "refund_percentage": 100,
            "reason": "Full refund window",
            "rule": "more_than_7d",
        }
