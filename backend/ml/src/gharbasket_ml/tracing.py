"""OpenTelemetry traces of the ML service (M0 §6.2), with the API's rules: exported over OTLP/HTTP
only when OTEL_EXPORTER_OTLP_ENDPOINT is set, and built here by hand so that no body, header
value, query string, raw path (OSRM's holds coordinates), id or exception message reaches a span.

- A request (`TracingMiddleware`): `<METHOD> <route template>`, the status and `gb.problem_code`
  (`record_problem`, from the exception handlers). The API's W3C `traceparent` is the parent, so
  one trace covers both services. None for `/health`, Render's health check.
- Outgoing HTTP (`AsyncTracedTransport`, `TracedTransport`): OSRM, the R2 registry and Anthropic,
  as `<METHOD> <host>` with the method, host and status. No trace context is sent to them.
- Logs: `TraceFormatter` adds `trace_id` and `span_id` to every line written inside a span.
"""

import hmac
import logging
import re
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any
from urllib.parse import unquote

import httpx2
from fastapi.telemetry import TelemetryConfig
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import Span, SpanKind, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from pydantic import SecretStr
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from gharbasket_ml import __version__
from gharbasket_ml.settings import Settings

log = logging.getLogger(__name__)
QUEUE_SIZE = 2048
EXPORT_TIMEOUT_S = 10.0
SHUTDOWN_S = 5.0
_HEADER_NAME = re.compile(r"[a-z0-9!#$%&'*+.^_`|~-]+")
ERROR_LOG_EVERY_S = 60.0
METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
# Render's health check polls /health every few seconds: no span. /health/ai (on demand) keeps one.
UNTRACED_PATHS = frozenset({"/health"})
# FastAPI's own OpenTelemetry is off: it would export traces, metrics and logs (with exception
# messages, raw paths and query strings) by itself once OTEL_EXPORTER_OTLP_ENDPOINT is set.
FASTAPI_TELEMETRY_OFF: TelemetryConfig = {
    "tracing": False,
    "metrics": False,
    "logs": False,
    "operation_spans": False,
    "auto_configure": False,
}

_tracer: trace.Tracer = trace.NoOpTracer()
_exporting: TracerProvider | None = None
_propagator = TraceContextTextMapPropagator()


class QuietExporter(SpanExporter):
    """A failed export is never raised, and logged at most once a minute."""

    def __init__(self, inner: SpanExporter) -> None:
        self._inner = inner
        self._logged_at: float | None = None

    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        try:
            result, reason = self._inner.export(spans), "export_failed"
        except Exception as exc:
            result, reason = SpanExportResult.FAILURE, type(exc).__name__
        now = time.monotonic()
        if result is SpanExportResult.FAILURE and (
            self._logged_at is None or now - self._logged_at >= ERROR_LOG_EVERY_S
        ):
            self._logged_at = now
            log.warning("trace export failed (%s, %d spans)", reason, len(spans))
        return result

    def shutdown(self) -> None:
        self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return self._inner.force_flush(timeout_millis)


def use_provider(provider: TracerProvider | None) -> None:
    global _tracer
    _tracer = provider.get_tracer("gharbasket_ml", __version__) if provider else trace.NoOpTracer()


def reset() -> None:
    """Back to recording nothing (tests)."""
    global _exporting
    _exporting = None
    use_provider(None)


def extract(traceparent: str) -> otel_context.Context:
    """The API's span as the parent, from its W3C `traceparent` (an empty context without one)."""
    return _propagator.extract({"traceparent": traceparent})


def otlp_headers(raw: str) -> dict[str, str] | None:
    """OTEL_EXPORTER_OTLP_HEADERS (`name=value,...`, values percent-encoded), parsed here and never
    logged; None when an entry is malformed or no `authorization` results. (The SDK's own parser
    logs a malformed entry whole, token included.)"""
    headers: dict[str, str] = {}
    for item in raw.split(","):
        name, sep, value = item.partition("=")
        name, value = name.strip().lower(), unquote(value.strip())
        if not sep or not value or not _HEADER_NAME.fullmatch(name):
            return None
        headers[name] = value
    return headers if "authorization" in headers else None


def setup(settings: Settings) -> TracerProvider | None:
    """Export when OTEL_EXPORTER_OTLP_ENDPOINT is set, once per process. Unreadable
    OTEL_EXPORTER_OTLP_HEADERS leave export off, logged without the value."""
    global _exporting
    if settings.otel_exporter_otlp_endpoint is None or _exporting is not None:
        return _exporting
    # The exporter also parses the environment's headers with the SDK's parser, which logs a
    # malformed entry whole: its logger is silenced before the exporter exists.
    logging.getLogger("opentelemetry.util.re").setLevel(logging.CRITICAL + 1)
    logging.getLogger("opentelemetry.exporter.otlp").setLevel(logging.CRITICAL)
    headers = None
    if settings.otel_exporter_otlp_headers is not None:
        headers = otlp_headers(settings.otel_exporter_otlp_headers.get_secret_value())
        if headers is None:
            log.warning("otel_headers_invalid: OTEL_EXPORTER_OTLP_HEADERS unreadable, export off")
            return None
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

    exporter = OTLPSpanExporter(
        endpoint=f"{settings.otel_exporter_otlp_endpoint.rstrip('/')}/v1/traces",
        headers=headers,
        timeout=EXPORT_TIMEOUT_S,
    )
    resource = Resource.create(
        {
            "service.name": settings.otel_service_name,
            "service.version": settings.git_commit or __version__,
            "deployment.environment": settings.app_env,
            **({"vcs.ref.head.revision": settings.git_commit} if settings.git_commit else {}),
        }
    )
    _exporting = TracerProvider(
        resource=resource,
        sampler=ParentBased(TraceIdRatioBased(settings.otel_traces_sampler_arg)),
        shutdown_on_exit=False,  # `shutdown` bounds it; the SDK's own exit can take 30 s
    )
    _exporting.add_span_processor(
        BatchSpanProcessor(QuietExporter(exporter), max_queue_size=QUEUE_SIZE)
    )
    use_provider(_exporting)
    return _exporting


def shutdown(timeout_s: float = SHUTDOWN_S) -> None:
    """Flush and stop the exporter within `timeout_s` (inside Render's 30 s grace)."""
    provider = _exporting
    if provider is None:
        return

    def stop() -> None:
        provider.force_flush(int(timeout_s * 1000))
        provider.shutdown()

    worker = threading.Thread(target=stop, name="trace-shutdown", daemon=True)
    worker.start()
    worker.join(timeout_s)


@contextmanager
def span(
    name: str, kind: SpanKind, attributes: Mapping[str, Any], parent: otel_context.Context | None
) -> Iterator[Span]:
    with _tracer.start_as_current_span(
        name,
        context=parent,
        kind=kind,
        attributes=attributes,
        record_exception=False,
        set_status_on_exception=False,
    ) as current:
        try:
            yield current
        except BaseException as exc:
            current.set_attribute("error.type", type(exc).__name__)  # never the message
            current.set_status(StatusCode.ERROR)
            raise


def record_problem(code: str, status: int) -> None:
    current = trace.get_current_span()
    current.set_attribute("gb.problem_code", code)
    if status >= 500:
        current.set_status(StatusCode.ERROR)


class TracingMiddleware:
    """The server span of each request, a child of the API's span when it sent `traceparent`
    with the service token. Without the token (or with a wrong one) the request starts a new
    trace: the service is on the public internet, which must not pick trace ids or force
    sampling. With no token configured (local development only) the context is honoured."""

    def __init__(self, app: ASGIApp, token: SecretStr | None = None) -> None:
        self.app = app
        self._expected = f"Bearer {token.get_secret_value()}".encode() if token else None

    def _trusted(self, authorization: bytes) -> bool:
        return self._expected is None or hmac.compare_digest(authorization, self._expected)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in UNTRACED_PATHS:
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        trusted = self._trusted(headers.get(b"authorization", b""))
        parent = extract(headers.get(b"traceparent", b"").decode("latin-1") if trusted else "")
        method = scope["method"] if scope["method"] in METHODS else "_OTHER"
        status = 500

        async def send_status(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        attributes = {"http.request.method": method}
        with span(method, SpanKind.SERVER, attributes, parent) as current:
            try:
                await self.app(scope, receive, send_status)
            finally:
                template = getattr(scope.get("route"), "path", None)
                if isinstance(template, str):
                    current.update_name(f"{method} {template}")
                    current.set_attribute("http.route", template)
                current.set_attribute("http.response.status_code", status)
                if status >= 500:
                    current.set_status(StatusCode.ERROR)


def _client_span(request: httpx2.Request) -> Any:
    host = request.url.host
    attributes = {"http.request.method": request.method, "server.address": host}
    return span(f"{request.method} {host}", SpanKind.CLIENT, attributes, None)


def _status(current: Span, response: httpx2.Response) -> None:
    current.set_attribute("http.response.status_code", response.status_code)
    if response.status_code >= 500:
        current.set_status(StatusCode.ERROR)


class AsyncTracedTransport(httpx2.AsyncBaseTransport):
    def __init__(self, inner: httpx2.AsyncBaseTransport | None = None) -> None:
        self._inner = inner or httpx2.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        with _client_span(request) as current:
            response = await self._inner.handle_async_request(request)
            _status(current, response)
            return response

    async def aclose(self) -> None:
        await self._inner.aclose()


class TracedTransport(httpx2.BaseTransport):
    def __init__(self, inner: httpx2.BaseTransport | None = None) -> None:
        self._inner = inner or httpx2.HTTPTransport()

    def handle_request(self, request: httpx2.Request) -> httpx2.Response:
        with _client_span(request) as current:
            response = self._inner.handle_request(request)
            _status(current, response)
            return response

    def close(self) -> None:
        self._inner.close()


class TraceFormatter(logging.Formatter):
    """Appends `trace_id=… span_id=…` to a line written inside a sampled span."""

    def format(self, record: logging.LogRecord) -> str:
        line = super().format(record)
        found = trace.get_current_span().get_span_context()
        if not found.is_valid or not found.trace_flags.sampled:
            return line
        return f"{line} trace_id={found.trace_id:032x} span_id={found.span_id:016x}"


def configure_logging() -> None:
    """The service's own log lines (WARNING and up, as before) with trace ids, unless something
    (pytest, a host) has already given the root logger a handler."""
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(TraceFormatter("%(levelname)s %(name)s: %(message)s"))
        root.addHandler(handler)
