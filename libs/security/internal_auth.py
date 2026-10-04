"""Shared-secret internal-mesh auth.

Not real mTLS — just enough to stop something outside the Docker network
from calling booking-service (etc.) directly and bypassing the two public
edges (gateway, agent-gateway). Every non-edge service verifies the header
on inbound requests; every service that calls a non-edge service attaches
it on outbound requests via the same shared secret.
"""

import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

INTERNAL_SECRET_HEADER = "X-Internal-Secret"

# Scraped by Prometheus and hit by container healthchecks without the
# secret, so they must stay open even on a locked-down service.
_EXEMPT_PATHS = {"/health", "/metrics", "/docs", "/openapi.json", "/redoc"}


def _get_secret() -> str:
    secret = os.environ.get("INTERNAL_SHARED_SECRET")
    if not secret:
        raise RuntimeError(
            "INTERNAL_SHARED_SECRET is not set — required for internal-mesh auth"
        )
    return secret


def internal_headers() -> dict[str, str]:
    """Header dict to attach to an outbound call to another internal service."""
    return {INTERNAL_SECRET_HEADER: _get_secret()}


def require_internal_secret(app: FastAPI) -> None:
    """Reject any request that doesn't carry the shared internal secret."""
    secret = _get_secret()

    @app.middleware("http")
    async def _internal_auth_middleware(request: Request, call_next):
        if request.url.path in _EXEMPT_PATHS:
            return await call_next(request)
        if request.headers.get(INTERNAL_SECRET_HEADER) != secret:
            return JSONResponse(
                status_code=403,
                content={
                    "error": {
                        "code": "INTERNAL_ACCESS_DENIED",
                        "message": "This endpoint is only reachable from within the TicketFlow service mesh.",
                    }
                },
            )
        return await call_next(request)
