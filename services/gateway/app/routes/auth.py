"""
Public authentication routes — no JWT required.

All endpoints proxy to the User Service and are subject only to the
per-IP sliding-window rate limit (to guard against credential stuffing
and registration spam without blocking unauthenticated callers).
"""

import structlog
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from app.config import get_settings
from app.routes.proxy import proxy_request

log = structlog.get_logger()
settings = get_settings()

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

_RATE_LIMITED_RESPONSE = JSONResponse(
    status_code=429,
    content={
        "error": {
            "code": "RATE_LIMITED",
            "message": "Too many requests",
            "details": {"retry_after": 60},
        }
    },
)


def _rate_limited_response() -> JSONResponse:
    """Return a fresh 429 response (headers cannot be mutated on a shared object)."""
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


@router.get("/health")
async def auth_health() -> dict:
    """Liveness probe — does not hit the User Service."""
    return {"status": "healthy", "service": "api-gateway"}


@router.post("/register")
async def register(request: Request) -> Response:
    """
    Proxy POST /api/v1/auth/register -> User Service /register.
    Subject to per-IP rate limit only.
    """
    rate_limiter = request.app.state.rate_limiter
    client_ip = request.client.host if request.client else "unknown"

    allowed, count = await rate_limiter.check_ip_limit(client_ip)
    if not allowed:
        log.warning(
            "ip_rate_limited",
            path="/api/v1/auth/register",
            client_ip=client_ip,
            count=count,
        )
        return _rate_limited_response()

    target = f"{settings.user_service_url}/register"
    correlation_id = getattr(request.state, "correlation_id", "")
    return await proxy_request(
        request,
        target,
        request.app.state.http_client,
        extra_headers={"X-Correlation-ID": correlation_id},
    )


@router.post("/login")
async def login(request: Request) -> Response:
    """
    Proxy POST /api/v1/auth/login -> User Service /login.
    Subject to per-IP rate limit only.
    """
    rate_limiter = request.app.state.rate_limiter
    client_ip = request.client.host if request.client else "unknown"

    allowed, count = await rate_limiter.check_ip_limit(client_ip)
    if not allowed:
        log.warning(
            "ip_rate_limited",
            path="/api/v1/auth/login",
            client_ip=client_ip,
            count=count,
        )
        return _rate_limited_response()

    target = f"{settings.user_service_url}/login"
    correlation_id = getattr(request.state, "correlation_id", "")
    return await proxy_request(
        request,
        target,
        request.app.state.http_client,
        extra_headers={"X-Correlation-ID": correlation_id},
    )


@router.post("/refresh")
async def refresh(request: Request) -> Response:
    """
    Proxy POST /api/v1/auth/refresh -> User Service /refresh.
    Subject to per-IP rate limit only.
    """
    rate_limiter = request.app.state.rate_limiter
    client_ip = request.client.host if request.client else "unknown"

    allowed, count = await rate_limiter.check_ip_limit(client_ip)
    if not allowed:
        log.warning(
            "ip_rate_limited",
            path="/api/v1/auth/refresh",
            client_ip=client_ip,
            count=count,
        )
        return _rate_limited_response()

    target = f"{settings.user_service_url}/refresh"
    correlation_id = getattr(request.state, "correlation_id", "")
    return await proxy_request(
        request,
        target,
        request.app.state.http_client,
        extra_headers={"X-Correlation-ID": correlation_id},
    )
