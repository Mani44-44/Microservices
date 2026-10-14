"""
Protected user-profile routes — JWT + dual rate limit required.

The caller's own bearer token is forwarded so the User Service
authenticates it and returns/updates only that user's profile.
"""

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import Response

from app.config import get_settings
from app.middleware.auth import extract_user_id_from_token, get_bearer_token
from app.routes.bookings import _common_headers, _enforce_limits
from app.routes.proxy import proxy_request

log = structlog.get_logger()
settings = get_settings()

router = APIRouter(prefix="/api/v1/users", tags=["users"])


async def _proxy_me(request: Request) -> Response:
    token = get_bearer_token(request)
    user_id = extract_user_id_from_token(token)

    limited = await _enforce_limits(request, user_id)
    if limited:
        return limited

    correlation_id = getattr(request.state, "correlation_id", "")
    return await proxy_request(
        request,
        f"{settings.user_service_url}/me",
        request.app.state.http_client,
        extra_headers=_common_headers(user_id, correlation_id),
    )


@router.get("/me")
async def get_me(request: Request) -> Response:
    """GET /api/v1/users/me  ->  User Service /me"""
    return await _proxy_me(request)


@router.patch("/me")
async def update_me(request: Request) -> Response:
    """PATCH /api/v1/users/me  ->  User Service /me (full_name / phone)"""
    return await _proxy_me(request)
