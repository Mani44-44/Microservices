"""
Structured request/response logging middleware using structlog.

Attaches correlation ID, method, path, status code, and duration to
every log line so traces can be correlated across services.
"""

import time
import uuid

import structlog
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

log = structlog.get_logger()


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    ASGI middleware that:
      - Generates or inherits an X-Correlation-ID per request.
      - Logs request start and completion with timing.
      - Attaches the correlation ID to the response headers.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
        request.state.correlation_id = correlation_id

        client_ip = request.client.host if request.client else "unknown"

        log.info(
            "request_received",
            method=request.method,
            path=request.url.path,
            client_ip=client_ip,
            correlation_id=correlation_id,
        )

        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            log.exception(
                "request_unhandled_error",
                method=request.method,
                path=request.url.path,
                duration_ms=duration_ms,
                correlation_id=correlation_id,
            )
            raise

        duration_ms = round((time.perf_counter() - start) * 1000, 2)
        response.headers["X-Correlation-ID"] = correlation_id

        log.info(
            "request_completed",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            duration_ms=duration_ms,
            client_ip=client_ip,
            correlation_id=correlation_id,
        )
        return response
