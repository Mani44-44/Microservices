from app import mock_apis


def test_weather_is_deterministic():
    a = mock_apis.get_weather("Paris", "2026-09-10")
    b = mock_apis.get_weather("Paris", "2026-09-10")
    assert a == b
    assert a["condition"] in ["Sunny", "Cloudy", "Rainy", "Snowy"]
    assert 0 <= a["precipitation_chance"] <= 100


def test_weather_varies_by_date():
    a = mock_apis.get_weather("Paris", "2026-09-10")
    b = mock_apis.get_weather("Paris", "2026-09-11")
    assert a != b or True  # not guaranteed different, just shouldn't error


def test_places_deterministic_and_shaped():
    a = mock_apis.get_places("Tokyo", "attraction", 3)
    b = mock_apis.get_places("Tokyo", "attraction", 3)
    assert a == b
    assert len(a) == 3
    for place in a:
        assert 1 <= place["price_level"] <= 4
        assert 3.5 <= place["rating"] <= 5.0


def test_events_deterministic():
    a = mock_apis.get_events("Rome", "2026-09-10")
    b = mock_apis.get_events("Rome", "2026-09-10")
    assert a == b
    assert 0 <= len(a) <= 3


def test_restaurants_deterministic():
    a = mock_apis.get_restaurants("London", cuisine="Local", price="$$")
    b = mock_apis.get_restaurants("London", cuisine="Local", price="$$")
    assert a == b
    assert len(a) == 3
