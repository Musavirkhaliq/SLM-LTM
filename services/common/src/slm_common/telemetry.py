from __future__ import annotations

import time

import structlog
from fastapi import FastAPI, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from slm_common.config import BaseServiceSettings, get_settings

_log = structlog.get_logger(__name__)

HTTP_REQUESTS = Counter(
    "slm_http_requests_total", "HTTP requests", ["service", "method", "route", "status"]
)
HTTP_LATENCY = Histogram(
    "slm_http_request_duration_seconds",
    "HTTP request latency",
    ["service", "method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120),
)
HTTP_IN_FLIGHT = Gauge("slm_http_in_flight_requests", "In-flight HTTP requests", ["service"])


class MetricsMiddleware:
    """Prometheus request count/latency labelled by route template (bounded cardinality)."""

    def __init__(self, app: ASGIApp, service: str) -> None:
        self.app = app
        self.service = service

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        status = 500
        started = time.perf_counter()
        HTTP_IN_FLIGHT.labels(self.service).inc()

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            HTTP_IN_FLIGHT.labels(self.service).dec()
            route = getattr(scope.get("route"), "path", None) or "unmatched"
            method = scope["method"]
            HTTP_REQUESTS.labels(self.service, method, route, str(status)).inc()
            HTTP_LATENCY.labels(self.service, method, route).observe(time.perf_counter() - started)


async def metrics_endpoint(_: Request) -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


def setup_otel(
    app: FastAPI, service: str, settings: BaseServiceSettings | None = None
) -> bool:
    """Export traces over OTLP/HTTP when SLM_OTLP_ENDPOINT is set. Returns whether enabled."""
    settings = settings or get_settings()
    if not settings.otlp_endpoint:
        return False
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(
        resource=Resource.create({"service.name": service, "deployment.environment": settings.env})
    )
    endpoint = settings.otlp_endpoint.rstrip("/") + "/v1/traces"
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
    _log.info("otel_enabled", endpoint=settings.otlp_endpoint)
    return True
