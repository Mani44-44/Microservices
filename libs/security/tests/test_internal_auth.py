import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from libs.security import INTERNAL_SECRET_HEADER, require_internal_secret

SECRET = "test-internal-secret"


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("INTERNAL_SHARED_SECRET", SECRET)
    app = FastAPI()
    require_internal_secret(app)

    @app.get("/thing")
    async def thing():
        return {"ok": True}

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app


async def _get(app, path, headers=None):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.get(path, headers=headers or {})


@pytest.mark.asyncio
async def test_accepts_correct_secret(app):
    assert (await _get(app, "/thing", {INTERNAL_SECRET_HEADER: SECRET})).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, "wrong", SECRET + "x", "sécret".encode("latin-1")])
async def test_rejects_missing_or_wrong_secret(app, value):
    headers = {} if value is None else {INTERNAL_SECRET_HEADER: value}
    resp = await _get(app, "/thing", headers)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "INTERNAL_ACCESS_DENIED"


@pytest.mark.asyncio
async def test_health_is_exempt(app):
    assert (await _get(app, "/health")).status_code == 200


def test_secret_comparison_is_constant_time():
    import inspect

    from libs.security import internal_auth

    assert "compare_digest" in inspect.getsource(internal_auth)
