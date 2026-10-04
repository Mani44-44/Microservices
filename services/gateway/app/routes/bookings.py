"""
Protected booking routes — JWT + dual rate limit required.

X-User-ID is always forwarded so the Booking Service can authorise
operations against the requesting user without issuing its own token lookup.
"""

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from app.config import get_settings
from app.middleware.auth import extract_user_id_from_token, get_bearer_token
from app.routes.proxy import proxy_request

log = structlog.get_logger()
settings = get_settings()

router = APIRouter(prefix="/api/v1/bookings", tags=["bookings"])


def _rate_limited_response() -> JSONResponse:
    return JSONResponse(
        status_code=429,
        content={
            "error": {
                "code": "RATE_LIMITED",
                "message": "Too many requests",
                "details": {"retry_after": 60},
            }
        },
        headers={"Retry-After": "60"},
    )


async def _enforce_limits(request: Request, user_id: str) -> JSONResponse | None:
    """
    Check both per-IP and per-user rate limits.

    Returns a 429 JSONResponse if either limit is exceeded, else None.
    """
    rate_limiter = request.app.state.rate_limiter
    client_ip = request.client.host if request.client else "unknown"

    ip_allowed, ip_count = await rate_limiter.check_ip_limit(client_ip)
    if not ip_allowed:
        log.warning(
            "ip_rate_limited",
            path=request.url.path,
            client_ip=client_ip,
            count=ip_count,
            user_id=user_id,
        )
        return _rate_limited_response()

    user_allowed, user_count = await rate_limiter.check_user_limit(user_id)
    if not user_allowed:
        log.warning(
            "user_rate_limited",
            path=request.url.path,
            user_id=user_id,
            count=user_count,
        )
        return _rate_limited_response()

    return None


def _common_headers(user_id: str, correlation_id: str) -> dict:
    return {
        "X-User-ID": user_id,
        "X-Correlation-ID": correlation_id,
    }


@router.post("/lock")
async def lock_booking(request: Request) -> Response:
    """
    POST /api/v1/bookings/lock  ->  Booking Service /bookings/lock

    Initiates a seat lock for the authenticated user.
    """
    token = get_bearer_token(request)
    user_id = extract_user_id_from_token(token)

    limited = await _enforce_limits(request, user_id)
    if limited:
        return limited

    correlation_id = getattr(request.state, "correlation_id", "")
    target = f"{settings.booking_service_url}/bookings/lock"
    return await proxy_request(
        request,
        target,
        request.app.state.http_client,
        extra_headers=_common_headers(user_id, correlation_id),
    )


@router.post("/{booking_id}/confirm")
async def confirm_booking(booking_id: str, request: Request) -> Response:
    """
    POST /api/v1/bookings/{booking_id}/confirm
      ->  Booking Service /bookings/{booking_id}/confirm

    Confirms a previously locked booking (triggers payment).
    """
    token = get_bearer_token(request)
    user_id = extract_user_id_from_token(token)

    limited = await _enforce_limits(request, user_id)
    if limited:
        return limited

    correlation_id = getattr(request.state, "correlation_id", "")
    target = f"{settings.booking_service_url}/bookings/{booking_id}/confirm"
    return await proxy_request(
        request,
        target,
        request.app.state.http_client,
        extra_headers=_common_headers(user_id, correlation_id),
    )


@router.post("/{booking_id}/release")
async def release_booking(booking_id: str, request: Request) -> Response:
    """
    POST /api/v1/bookings/{booking_id}/release
      ->  Booking Service /bookings/{booking_id}/release

    Releases a seat lock without charging the user.
    """
    token = get_bearer_token(request)
    user_id = extract_user_id_from_token(token)

    limited = await _enforce_limits(request, user_id)
    if limited:
        return limited

    correlation_id = getattr(request.state, "correlation_id", "")
    target = f"{settings.booking_service_url}/bookings/{booking_id}/release"
    return await proxy_request(
        request,
        target,
        request.app.state.http_client,
        extra_headers=_common_headers(user_id, correlation_id),
    )


@router.get("/{booking_id}")
async def get_booking(booking_id: str, request: Request) -> Response:
    """
    GET /api/v1/bookings/{booking_id}
      ->  Booking Service /bookings/{booking_id}

    Retrieves booking details for the authenticated user.
    """
    token = get_bearer_token(request)
    user_id = extract_user_id_from_token(token)

    limited = await _enforce_limits(request, user_id)
    if limited:
        return limited

    correlation_id = getattr(request.state, "correlation_id", "")
    target = f"{settings.booking_service_url}/bookings/{booking_id}"
    return await proxy_request(
        request,
        target,
        request.app.state.http_client,
        extra_headers=_common_headers(user_id, correlation_id),
    )
