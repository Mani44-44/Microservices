"""
auth.py
~~~~~~~
JWT decode-only utility for the booking service.
Tokens are issued by the user-service; we only validate them here.
"""
from typing import Optional
from uuid import UUID

import jwt
import structlog
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import get_settings

log = structlog.get_logger()
settings = get_settings()

_bearer_scheme = HTTPBearer(auto_error=True)


def _decode_token(token: str) -> dict:
    """Decode and validate a JWT, raising HTTPException on failure."""
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
                    "message": "Access token has expired",
                    "details": {},
                }
            },
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "INVALID_TOKEN",
                    "message": f"Invalid access token: {exc}",
                    "details": {},
                }
            },
        )


async def get_current_user_id(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> UUID:
    """FastAPI dependency – returns the authenticated user's UUID."""
    payload = _decode_token(credentials.credentials)
    sub: Optional[str] = payload.get("sub")
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "INVALID_TOKEN",
                    "message": "Token missing subject claim",
                    "details": {},
                }
            },
        )
    try:
        return UUID(sub)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "INVALID_TOKEN",
                    "message": "Token subject is not a valid UUID",
                    "details": {},
                }
            },
        )


async def get_current_user_role(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
) -> str:
    """FastAPI dependency – returns the authenticated user's role.

    Defaults to 'customer' for tokens issued before the role claim existed.
    """
    payload = _decode_token(credentials.credentials)
    return payload.get("role", "customer")
