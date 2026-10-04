from datetime import datetime, timedelta, timezone

import pytest

from app.rules_engine import RefundRulesEngine

engine = RefundRulesEngine()
NOW = datetime(2026, 8, 30, 12, 0, 0, tzinfo=timezone.utc)


def _check(delta: timedelta, status: str = "PUBLISHED"):
    return engine.check_eligibility(NOW + delta, status, NOW)


def test_more_than_7_days_full_refund():
    result = _check(timedelta(days=8))
    assert result["refund_percentage"] == 100
    assert result["rule"] == "more_than_7d"


def test_within_7_days_partial_refund():
    result = _check(timedelta(days=3))
    assert result["refund_percentage"] == 50
    assert result["rule"] == "within_7d"


def test_within_24_hours_no_refund():
    result = _check(timedelta(hours=5))
    assert result["refund_percentage"] == 0
    assert result["rule"] == "within_24h"


def test_event_cancelled_by_organizer_full_refund_regardless_of_date():
    result = _check(timedelta(hours=1), status="CANCELLED")
    assert result["refund_percentage"] == 100
    assert result["rule"] == "event_cancelled"

    # even far in the future, cancellation always wins
    result2 = _check(timedelta(days=30), status="CANCELLED")
    assert result2["refund_percentage"] == 100
    assert result2["rule"] == "event_cancelled"


def test_boundary_exactly_24_hours_is_within_24h_bucket():
    result = _check(timedelta(hours=24))
    assert result["refund_percentage"] == 0
    assert result["rule"] == "within_24h"


def test_boundary_just_over_24_hours_is_within_7d_bucket():
    result = _check(timedelta(hours=24, seconds=1))
    assert result["refund_percentage"] == 50
    assert result["rule"] == "within_7d"


def test_boundary_exactly_7_days_is_within_7d_bucket():
    result = _check(timedelta(days=7))
    assert result["refund_percentage"] == 50
    assert result["rule"] == "within_7d"


def test_boundary_just_over_7_days_is_full_refund():
    result = _check(timedelta(days=7, seconds=1))
    assert result["refund_percentage"] == 100
    assert result["rule"] == "more_than_7d"


def test_event_already_passed_treated_as_within_24h():
    result = _check(timedelta(hours=-2))
    assert result["refund_percentage"] == 0
    assert result["rule"] == "within_24h"
