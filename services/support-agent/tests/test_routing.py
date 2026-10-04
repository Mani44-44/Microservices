from app.routing import extract_amount, extract_booking_id


def test_extract_booking_id_found():
    msg = "Please cancel booking 3fa85f64-5717-4562-b3fc-2c963f66afa6 for me"
    assert extract_booking_id(msg) == "3fa85f64-5717-4562-b3fc-2c963f66afa6"


def test_extract_booking_id_missing():
    assert extract_booking_id("What is your refund policy?") is None


def test_extract_amount_found():
    assert extract_amount("I want $45.50 back please") == 45.50


def test_extract_amount_missing():
    assert extract_amount("I want a refund") is None
