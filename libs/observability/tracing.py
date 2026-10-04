"""OpenTelemetry tracing setup, shared across every TicketFlow service.

Exports spans to the all-in-one Jaeger container via OTLP/HTTP. Also binds
trace_id/span_id into structlog's contextvars so every log line emitted
during a request correlates with its Jaeger trace — this is the project's
deliberate substitute for a separate ELK stack (see README).
"""

import os

import structlog
from fastapi import FastAPI, Request
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

_JAEGER_OTLP_ENDPOINT = os.environ.get(
    "JAEGER_OTLP_ENDPOINT", "http://jaeger:4318/v1/traces"
)


def instrument_tracing(app: FastAPI, service_name: str) -> None:
    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: service_name}))
    provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=_JAEGER_OTLP_ENDPOINT))
    )
    trace.set_tracer_provider(provider)

    FastAPIInstrumentor.instrument_app(app)

    @app.middleware("http")
    async def _trace_context_middleware(request: Request, call_next):
        span = trace.get_current_span()
        span_context = span.get_span_context()
        if span_context.is_valid:
            structlog.contextvars.bind_contextvars(
                trace_id=format(span_context.trace_id, "032x"),
                span_id=format(span_context.span_id, "016x"),
            )
        return await call_next(request)
