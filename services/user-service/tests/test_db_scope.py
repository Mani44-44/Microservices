from app.database import get_db
from app.main import app
from libs.testing import routes_committing_after_response


def test_db_session_commits_before_response_is_sent():
    """get_db commits after `yield`; with FastAPI's default dependency scope
    that runs after the response is sent, so a client could get 201 for a
    booking/payment that isn't committed yet (seen under load as a confirm
    returning 404 for a booking that had just been created)."""
    assert routes_committing_after_response(app, get_db) == []
