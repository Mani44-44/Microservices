"""Comprehensive tests for the User Service authentication endpoints."""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import User


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _register(client: AsyncClient, data: dict) -> dict:
    """Register a user and return the parsed JSON body."""
    resp = await client.post("/register", json=data)
    return resp


async def _login(client: AsyncClient, email: str, password: str):
    return await client.post("/login", json={"email": email, "password": password})


# ---------------------------------------------------------------------------
# Registration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_success(client: AsyncClient, test_user_data: dict):
    """POST /register should return 201 with a valid token pair."""
    resp = await _register(client, test_user_data)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert "access_token" in body
    assert "refresh_token" in body
    assert body["token_type"] == "bearer"
    assert isinstance(body["expires_in"], int) and body["expires_in"] > 0


@pytest.mark.asyncio
async def test_register_duplicate_email(client: AsyncClient, test_user_data: dict):
    """Second registration with the same email should return 409."""
    # Register once (may already exist from another test; ignore first result)
    await _register(client, test_user_data)
    # Register again with same email
    resp = await _register(client, test_user_data)
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["detail"]["error"]["code"] == "EMAIL_ALREADY_EXISTS"


@pytest.mark.asyncio
async def test_register_weak_password(client: AsyncClient, test_user_data: dict):
    """Passwords shorter than 8 characters must be rejected with 422."""
    weak = {**test_user_data, "email": "weak@example.com", "password": "short"}
    resp = await _register(client, weak)
    assert resp.status_code == 422, resp.text


# ---------------------------------------------------------------------------
# Login tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_success(client: AsyncClient, test_user_data: dict):
    """POST /login should return 200 with a token pair for valid credentials."""
    # Ensure user exists
    data = {**test_user_data, "email": "login_ok@example.com"}
    reg = await _register(client, data)
    assert reg.status_code == 201

    resp = await _login(client, data["email"], data["password"])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "access_token" in body
    assert "refresh_token" in body


@pytest.mark.asyncio
async def test_login_wrong_password(client: AsyncClient, test_user_data: dict):
    """Wrong password must return 401."""
    data = {**test_user_data, "email": "wrong_pw@example.com"}
    await _register(client, data)

    resp = await _login(client, data["email"], "wrongpassword!")
    assert resp.status_code == 401, resp.text
    assert resp.json()["detail"]["error"]["code"] == "INVALID_CREDENTIALS"


@pytest.mark.asyncio
async def test_login_unknown_email(client: AsyncClient):
    """Login with a non-existent email must return 401."""
    resp = await _login(client, "nobody@example.com", "somepassword")
    assert resp.status_code == 401, resp.text
    assert resp.json()["detail"]["error"]["code"] == "INVALID_CREDENTIALS"


# ---------------------------------------------------------------------------
# Refresh token tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_refresh_token_success(client: AsyncClient, test_user_data: dict):
    """POST /refresh with a valid refresh token must return a new token pair."""
    data = {**test_user_data, "email": "refresh_ok@example.com"}
    reg = await _register(client, data)
    assert reg.status_code == 201
    old_refresh = reg.json()["refresh_token"]

    resp = await client.post("/refresh", json={"refresh_token": old_refresh})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "access_token" in body
    assert "refresh_token" in body
    # New token must differ from the old one
    assert body["refresh_token"] != old_refresh


@pytest.mark.asyncio
async def test_refresh_token_reuse_fails(client: AsyncClient, test_user_data: dict):
    """Using the same refresh token twice must return 401 on the second attempt."""
    data = {**test_user_data, "email": "reuse@example.com"}
    reg = await _register(client, data)
    assert reg.status_code == 201
    original_refresh = reg.json()["refresh_token"]

    # First use – should succeed
    first = await client.post("/refresh", json={"refresh_token": original_refresh})
    assert first.status_code == 200, first.text

    # Second use with the *same* original token – must fail
    second = await client.post("/refresh", json={"refresh_token": original_refresh})
    assert second.status_code == 401, second.text
    code = second.json()["detail"]["error"]["code"]
    assert code in ("REFRESH_TOKEN_REUSE", "INVALID_REFRESH_TOKEN")


@pytest.mark.asyncio
async def test_refresh_token_reuse_revokes_family(client: AsyncClient, test_user_data: dict):
    """Reuse of a rotated-out token must revoke the whole family, not just itself."""
    data = {**test_user_data, "email": "reuse_family@example.com"}
    reg = await _register(client, data)
    assert reg.status_code == 201
    token_a = reg.json()["refresh_token"]

    # A -> B (A revoked)
    first = await client.post("/refresh", json={"refresh_token": token_a})
    assert first.status_code == 200, first.text
    token_b = first.json()["refresh_token"]

    # Reuse A -> triggers reuse detection, should revoke B too
    reuse = await client.post("/refresh", json={"refresh_token": token_a})
    assert reuse.status_code == 401, reuse.text

    # B must now also be rejected, even though it was never reused itself
    second = await client.post("/refresh", json={"refresh_token": token_b})
    assert second.status_code == 401, second.text


# ---------------------------------------------------------------------------
# Logout tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_logout_revokes_token(client: AsyncClient, test_user_data: dict):
    """After logout, trying to use the refresh token must return 401."""
    data = {**test_user_data, "email": "logout@example.com"}
    reg = await _register(client, data)
    assert reg.status_code == 201
    refresh_token = reg.json()["refresh_token"]

    # Logout
    logout_resp = await client.post("/logout", json={"refresh_token": refresh_token})
    assert logout_resp.status_code == 200, logout_resp.text

    # Attempt to refresh with the revoked token
    refresh_resp = await client.post("/refresh", json={"refresh_token": refresh_token})
    assert refresh_resp.status_code == 401, refresh_resp.text


# ---------------------------------------------------------------------------
# /me tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_me_authenticated(client: AsyncClient, test_user_data: dict):
    """GET /me with a valid Bearer token should return the user profile."""
    data = {**test_user_data, "email": "me_auth@example.com"}
    reg = await _register(client, data)
    assert reg.status_code == 201
    access_token = reg.json()["access_token"]

    resp = await client.get("/me", headers={"Authorization": f"Bearer {access_token}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["email"] == data["email"]
    assert body["full_name"] == data["full_name"]
    assert "id" in body
    assert body["is_active"] is True


@pytest.mark.asyncio
async def test_get_me_unauthenticated(client: AsyncClient):
    """GET /me without an Authorization header must return 401."""
    resp = await client.get("/me")
    assert resp.status_code == 401, resp.text


@pytest.mark.asyncio
async def test_pii_encrypted_at_rest(client: AsyncClient, test_user_data: dict, test_db_engine):
    """full_name/phone must be stored as Fernet ciphertext, not plaintext."""
    data = {**test_user_data, "email": "encrypted_at_rest@example.com"}
    reg = await _register(client, data)
    assert reg.status_code == 201

    session_factory = async_sessionmaker(bind=test_db_engine, class_=AsyncSession)
    async with session_factory() as session:
        row = (
            await session.execute(select(User).where(User.email == data["email"]))
        ).scalar_one()
        assert row.full_name != data["full_name"]
        assert row.phone != data["phone"]

    # And the API still returns the decrypted plaintext
    access_token = reg.json()["access_token"]
    resp = await client.get("/me", headers={"Authorization": f"Bearer {access_token}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["full_name"] == data["full_name"]
    assert body["phone"] == data["phone"]


# ---------------------------------------------------------------------------
# Profile update tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_profile(client: AsyncClient, test_user_data: dict):
    """PATCH /me should update full_name and return the updated profile."""
    data = {**test_user_data, "email": "update_me@example.com"}
    reg = await _register(client, data)
    assert reg.status_code == 201
    access_token = reg.json()["access_token"]

    new_name = "Alice Updated"
    resp = await client.patch(
        "/me",
        json={"full_name": new_name},
        headers={"Authorization": f"Bearer {access_token}"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["full_name"] == new_name
    assert body["email"] == data["email"]
