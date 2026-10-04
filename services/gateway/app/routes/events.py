"""
Protected event routes — JWT + dual rate limit required.

Every endpoint validates the bearer token before touching Redis or the
Catalog Service.  Both per-user and per-IP limits are checked; whichever
is exhausted first triggers a 429.
"""

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from app.config import get_settings
from app.middleware.auth import extract_user_id_from_token, get_bearer_token
from app.routes.proxy import proxy_request

log = structlog.get_logger()
settings = get_settings()

router = APIRouter(prefix="/api/v1/events", tags=["events"])


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


@router.get("/")
async def list_events(request: Request) -> Response:
    """
    GET /api/v1/events  ->  Catalog Service /events
    Returns paginated event list; all query params are forwarded verbatim.
    """
    token = get_bearer_token(request)
    user_id = extract_user_id_from_token(token)

    limited = await _enforce_limits(request, user_id)
    if limited:
        return limited

    # Trailing slash required: catalog-service's router is mounted at
    # prefix="/events" with the list route at "/", so a bare "/events"
    # 307-redirects to catalog-service's internal Docker hostname, which
    # the gateway's httpx client (no follow_redirects) can't reach externally.
    target = f"{settings.catalog_service_url}/events/"
    correlation_id = getattr(request.state, "correlation_id", "")
    return await proxy_request(
        request,
        target,
        request.app.state.http_client,
        extra_headers={
            "X-User-ID": user_id,
            "X-Correlation-ID": correlation_id,
        },
    )


@router.get("/{event_id}")
async def get_event(event_id: str, request: Request) -> Response:
    """
    GET /api/v1/events/{event_id}  ->  Catalog Service /events/{event_id}
    """
    token = get_bearer_token(request)
    user_id = extract_user_id_from_token(token)

    limited = await _enforce_limits(request, user_id)
    if limited:
        return limited

    target = f"{settings.catalog_service_url}/events/{event_id}"
    correlation_id = getattr(request.state, "correlation_id", "")
    return await proxy_request(
        request,
        target,
        request.app.state.http_client,
        extra_headers={
            "X-User-ID": user_id,
            "X-Correlation-ID": correlation_id,
        },
    )


@router.get("/{event_id}/seats")
async def get_event_seats(event_id: str, request: Request) -> Response:
    """
    GET /api/v1/events/{event_id}/seats  ->  Catalog Service /events/{event_id}/seats
    """
    token = get_bearer_token(request)
    user_id = extract_user_id_from_token(token)

    limited = await _enforce_limits(request, user_id)
    if limited:
        return limited

    target = f"{settings.catalog_service_url}/events/{event_id}/seats"
    correlation_id = getattr(request.state, "correlation_id", "")
    return await proxy_request(
        request,
        target,
        request.app.state.http_client,
        extra_headers={
            "X-User-ID": user_id,
            "X-Correlation-ID": correlation_id,
        },
    )
