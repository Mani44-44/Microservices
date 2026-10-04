"""
JWT validation helpers used by protected route dependencies.

All token errors surface as 401 HTTPExceptions with the standard
TicketFlow error envelope so consumers get consistent error shapes.
"""

import jwt
from fastapi import HTTPException, Request

from app.config import get_settings

settings = get_settings()


def extract_user_id_from_token(token: str) -> str:
    """
    Decode and validate a JWT bearer token.

    Returns the ``sub`` claim (user ID string) on success.
    Raises HTTPException 401 on any validation failure.
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret_key,
            algorithms=[settings.jwt_algorithm],
        )
        user_id: str | None = payload.get("sub")
        if not user_id:
            raise HTTPException(
                status_code=401,
                detail={
                    "error": {
                        "code": "INVALID_TOKEN",
                        "message": "Token missing subject claim",
                    }
                },
            )
        return user_id
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "code": "TOKEN_EXPIRED",
                    "message": "Access token has expired",
                }
            },
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "code": "INVALID_TOKEN",
                    "message": "Invalid access token",
                }
            },
        )


def get_bearer_token(request: Request) -> str:
    """
    Extract the raw token string from the ``Authorization: Bearer <token>`` header.

    Raises HTTPException 401 when the header is absent or malformed.
    """
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "code": "MISSING_TOKEN",
                    "message": "Authorization header required",
                }
            },
        )
    return auth[7:]
