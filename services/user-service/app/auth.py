"""Authentication utilities for the User Service.

Covers:
- Password hashing / verification (bcrypt, cost 12)
- Opaque refresh-token generation and SHA-256 hashing
- JWT access-token creation and decoding (PyJWT)
"""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import HTTPException, status

from app.config import get_settings

settings = get_settings()


# ---------------------------------------------------------------------------
# Password helpers
# ---------------------------------------------------------------------------


def hash_password(password: str) -> str:
    """Return a bcrypt hash of *password* using work factor 12.

    bcrypt.hashpw expects bytes so we encode the plain-text password first.
    The returned hash is stored as a UTF-8 string.
    """
    salt = bcrypt.gensalt(rounds=12)
    hashed = bcrypt.hashpw(password.encode("utf-8"), salt)
    return hashed.decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """Return *True* when *password* matches *hashed* (bcrypt checkpw)."""
    return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))


# ---------------------------------------------------------------------------
# Refresh-token helpers
# ---------------------------------------------------------------------------


def generate_refresh_token() -> str:
    """Return a 43-character URL-safe random opaque token (256 bits of entropy)."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Return the lowercase hex SHA-256 digest of *token* (64 characters).

    Only the hash is persisted; the raw token is sent to the client and never
    stored, preventing disclosure if the DB is compromised.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------


def create_access_token(user_id: uuid.UUID, email: str, role: str = "customer") -> str:
    """Encode a short-lived JWT access token signed with HS256.

    Claims:
        sub  – stringified UUID of the user
        email – user's email address
        role – 'customer' or 'admin'
        iat  – issued-at timestamp
        exp  – expiry timestamp (now + jwt_access_token_expire_minutes)
    """
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.jwt_access_token_expire_minutes)
    payload = {
        "sub": str(user_id),
        "email": email,
        "role": role,
        "iat": now,
        "exp": expire,
    }
    return jwt.encode(
        payload,
        settings.jwt_secret_key,
        algorithm=settings.jwt_algorithm,
    )


def decode_access_token(token: str) -> dict:
    """Decode and verify a JWT access token.

    Returns the decoded payload dict on success.
    Raises ``HTTPException(401)`` for expired or otherwise invalid tokens.
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "TOKEN_EXPIRED",
                    "message": "Access token has expired.",
                    "details": {},
                    "correlation_id": "",
                }
            },
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "TOKEN_INVALID",
                    "message": f"Invalid access token: {exc}",
                    "details": {},
                    "correlation_id": "",
                }
            },
        )
