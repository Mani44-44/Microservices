from datetime import date, timedelta

from app.constraint_extractor import ConstraintExtractor


def test_full_info_in_one_message():
    extractor = ConstraintExtractor()
    result = extractor.extract(
        "I want to go to Paris from 2026-09-10 to 2026-09-15 with a budget of $2000 "
        "for 2 people, we love food and culture."
    )
    assert result["destination"] == "Paris"
    assert result["start_date"] == "2026-09-10"
    assert result["end_date"] == "2026-09-15"
    assert result["budget"] == 2000.0
    assert result["num_travelers"] == 2
    assert "food" in result["interests"]
    assert "culture" in result["interests"]
    assert result["missing_required_fields"] == []


def test_partial_info_requires_follow_up():
    extractor = ConstraintExtractor()
    result = extractor.extract("I want to visit Tokyo.")
    assert result["destination"] == "Tokyo"
    assert result["missing_required_fields"] == ["start_date", "end_date"]


def test_merging_constraints_across_two_calls():
    extractor = ConstraintExtractor()
    first = extractor.extract("I want to go to Rome, budget $1500.")
    assert first["missing_required_fields"] == ["start_date", "end_date"]

    second = extractor.extract("Let's go for 3 days starting 2026-10-01.", first)
    assert second["destination"] == "Rome"
    assert second["budget"] == 1500.0
    assert second["start_date"] == "2026-10-01"
    assert second["end_date"] == "2026-10-03"
    assert second["missing_required_fields"] == []


def test_ambiguous_no_destination():
    extractor = ConstraintExtractor()
    result = extractor.extract("I want a relaxation vegetarian trip somewhere nice.")
    assert result["destination"] is None
    assert "relaxation" in result["interests"]
    assert "vegetarian" in result["dietary_restrictions"]
    assert "destination" in result["missing_required_fields"]


def test_relative_dates_next_week():
    extractor = ConstraintExtractor()
    result = extractor.extract("Barcelona next week for 5 days.")
    expected_start = date.today() + timedelta(days=7)
    expected_end = expected_start + timedelta(days=4)
    assert result["start_date"] == expected_start.isoformat()
    assert result["end_date"] == expected_end.isoformat()


def test_interests_merge_as_union_not_overwrite():
    extractor = ConstraintExtractor()
    first = extractor.extract("Tokyo, I love food.")
    second = extractor.extract("Also into history and shopping.", first)
    assert set(second["interests"]) == {"food", "history", "shopping"}
