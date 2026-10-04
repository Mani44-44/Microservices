"""Prometheus request metrics — shared across every TicketFlow service.

One Counter/Histogram pair, labelled by service so all 13 services can share
the same process-wide registry without name collisions when scraped.
"""

import time

from fastapi import FastAPI, Request
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from starlette.responses import Response

REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["service", "method", "path", "status"],
)

REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["service", "method", "path"],
)

DLQ_MESSAGES = Counter(
    "dlq_messages_total",
    "Messages routed to a dead-letter topic after exhausting retries",
    ["service", "topic"],
)


def instrument_metrics(app: FastAPI, service_name: str) -> None:
    """Add a request-timing middleware and a /metrics scrape endpoint."""

    @app.middleware("http")
    async def _metrics_middleware(request: Request, call_next):
        start = time.perf_counter()
        response = await call_next(request)
        duration = time.perf_counter() - start

        # Use the matched route template (e.g. "/bookings/{id}"), not the raw
        # path, so per-request IDs don't explode the label cardinality.
        route = request.scope.get("route")
        path = route.path if route is not None else request.url.path

        REQUEST_COUNT.labels(
            service=service_name,
            method=request.method,
            path=path,
            status=response.status_code,
        ).inc()
        REQUEST_LATENCY.labels(
            service=service_name, method=request.method, path=path
        ).observe(duration)

        return response

    @app.get("/metrics", include_in_schema=False)
    async def _metrics():
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
