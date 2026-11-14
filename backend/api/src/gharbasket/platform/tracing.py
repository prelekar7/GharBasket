"""OpenTelemetry traces (M0 §6.2), exported over OTLP/HTTP only when OTEL_EXPORTER_OTLP_ENDPOINT
is set; tests and local runs export nothing.

The spans are built here, not by the contrib instrumentations, so the never-list holds whatever
their defaults become: no body, no header value, no query string, no raw path (the route template
only), no SQL parameter (literals in SQL text become `?` too, and a statement whose literals cannot
be told apart is reduced to its keyword), no id or personal data, and an exception only as its
type and the problem `code`, never its message.

- A request (`request_span`, opened by `RequestContextMiddleware`): `<METHOD> <route template>`,
  the status, `gb.request_id` and `gb.problem_code` (set by `errors.problem_response`); none for
  the polled liveness check, `/v1/health`.
- A database call (`instrument_engine`, SQLAlchemy's cursor events): `db.query.text`.
- Outgoing HTTP (`http_client()`): `<METHOD> <host>` with the method, host and status. The W3C
  `traceparent` goes to the ML service only, so one trace covers both services. The S3 store
  (boto3) gets the same spans from `instrument_boto`.
- A job run and a consumer (`span`, in platform/jobs.py and platform/outbox.py); background work a
  request starts (the after-commit relay, a route solve) is a trace of its own (`root_span`),
  linked to the request's span.
- Logs: `platform.logging.add_trace_ids` puts `trace_id` and `span_id` on every line in a sampled
  span.

A missing or unreachable collector changes nothing: spans wait in a bounded queue (a full one
drops them), an export error is logged at most once a minute, and shutting down waits 5 s at most.
"""

import logging
import re
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any
from urllib.parse import unquote, urlsplit

import httpx2
import structlog
from fastapi.telemetry import TelemetryConfig
from opentelemetry import context as otel_context
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import Link, Span, SpanContext, SpanKind, StatusCode
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from sqlalchemy import event
from sqlalchemy.engine import Engine, ExceptionContext

from gharbasket import __version__
from gharbasket.platform.settings import Settings

log = structlog.get_logger("gharbasket.tracing")
QUEUE_SIZE = 2048  # spans waiting for export; more are dropped
EXPORT_TIMEOUT_S = 10.0
SHUTDOWN_S = 5.0
ERROR_LOG_EVERY_S = 60.0
SQL_MAX = 2000
METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
# Render's health check and UptimeRobot poll the liveness check: a span each would be most of the
# traces. Exact paths only; /v1/ready, called on demand, keeps its span.
UNTRACED_PATHS = frozenset({"/v1/health"})
# Quoted strings, then numbers (hex, exponent, leading dot); `$1` placeholders and names like `t1`
# stay.
_SQL_LITERALS = re.compile(
    r"'(?:[^']|'')*'"
    r"|(?<![\w$.])(?:0[xX][0-9a-fA-F]+|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)(?![\w.])"
)
# Forms whose literals the pattern above cannot pair reliably: dollar quoting, E'' strings with
# backslash escapes, and comments. A statement with one is reduced to its keyword.
_SQL_OPAQUE = re.compile(r"\$\$|\$[A-Za-z_]\w*\$|(?<![\w'])[eE]'|--|/\*")
_HEADER_NAME = re.compile(r"[a-z0-9!#$%&'*+.^_`|~-]+")
# FastAPI's own OpenTelemetry (0.142+) is off: it would export traces, metrics and logs (with
# exception messages) on its own as soon as OTEL_EXPORTER_OTLP_ENDPOINT is set, and record the raw
# path and query string. Every span is built here instead.
FASTAPI_TELEMETRY_OFF: TelemetryConfig = {
    "tracing": False,
    "metrics": False,
    "logs": False,
    "operation_spans": False,
    "auto_configure": False,
}

_tracer: trace.Tracer = trace.NoOpTracer()
_exporting: TracerProvider | None = None
_propagate_to: frozenset[str] = frozenset()
_propagator = TraceContextTextMapPropagator()


class QuietExporter(SpanExporter):
    """Wraps the OTLP exporter: a failure is never raised and is logged at most once a minute
    (the exporter's own error lines are silenced; they would come every few seconds)."""

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
            log.warning("trace_export_failed", reason=reason, spans=len(spans))
        return result

    def shutdown(self) -> None:
        self._inner.shutdown()

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return self._inner.force_flush(timeout_millis)


def use_provider(provider: TracerProvider | None) -> None:
    """Spans go to `provider` from now on; None: nothing is recorded (tests use this)."""
    global _tracer
    _tracer = provider.get_tracer("gharbasket", __version__) if provider else trace.NoOpTracer()


def reset() -> None:
    """Back to recording nothing, and forget the exporting provider (tests)."""
    global _exporting
    _exporting = None
    use_provider(None)


def otlp_headers(raw: str) -> dict[str, str] | None:
    """OTEL_EXPORTER_OTLP_HEADERS (`name=value,...`, values percent-encoded) parsed here, never
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
    """Export to OTEL_EXPORTER_OTLP_ENDPOINT when it is set, once per process; otherwise tracing
    stays as it is (off, or a test's provider). Returns the exporting provider. Unreadable
    OTEL_EXPORTER_OTLP_HEADERS leave export off, logged without the value."""
    global _exporting, _propagate_to
    if settings.ml_service_url and (host := urlsplit(settings.ml_service_url).hostname):
        _propagate_to = frozenset({host})
    if settings.otel_exporter_otlp_endpoint is None or _exporting is not None:
        return _exporting
    # The exporter also reads the environment's headers with the SDK's parser, which logs a
    # malformed entry whole: its logger is silenced before the exporter exists.
    logging.getLogger("opentelemetry.util.re").setLevel(logging.CRITICAL + 1)
    logging.getLogger("opentelemetry.exporter.otlp").setLevel(logging.CRITICAL)
    headers = None
    if settings.otel_exporter_otlp_headers is not None:
        headers = otlp_headers(settings.otel_exporter_otlp_headers.get_secret_value())
        if headers is None:
            log.warning("otel_headers_invalid", variable="OTEL_EXPORTER_OTLP_HEADERS")
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
    """Flush and stop the exporter within `timeout_s` (the app's shutdown, inside Render's 30 s
    grace): spans still queued after that are dropped, never waited for."""
    provider = _exporting
    if provider is None:
        return

    def stop() -> None:
        provider.force_flush(int(timeout_s * 1000))
        provider.shutdown()

    worker = threading.Thread(target=stop, name="trace-shutdown", daemon=True)
    worker.start()
    worker.join(timeout_s)


def _fail(span: Span, exc: BaseException) -> None:
    """The exception's type only: its message can carry values."""
    span.set_attribute("error.type", type(exc).__name__)
    span.set_status(StatusCode.ERROR)


@contextmanager
def span(
    name: str, attributes: Mapping[str, Any] | None = None, kind: SpanKind = SpanKind.INTERNAL
) -> Iterator[Span]:
    """A span of our own (a job run, a consumer); an exception is recorded by its type."""
    with _tracer.start_as_current_span(
        name,
        kind=kind,
        attributes=attributes,
        record_exception=False,
        set_status_on_exception=False,
    ) as current:
        try:
            yield current
        except BaseException as exc:
            _fail(current, exc)
            raise


def current_context() -> SpanContext:
    """The current span's context, to link background work to the request that started it."""
    return trace.get_current_span().get_span_context()


@contextmanager
def root_span(name: str, link_to: SpanContext | None = None) -> Iterator[Span]:
    """A trace of its own for background work a request started (it may outlive the request and
    handle other requests' rows), linked to the request's span."""
    links = [Link(link_to)] if link_to is not None and link_to.is_valid else None
    token = otel_context.attach(otel_context.Context())
    try:
        with _tracer.start_as_current_span(
            name, links=links, record_exception=False, set_status_on_exception=False
        ) as current:
            try:
                yield current
            except BaseException as exc:
                _fail(current, exc)
                raise
    finally:
        otel_context.detach(token)


def record_problem(code: str, status: int) -> None:
    """The RFC 9457 answer's `code` on the request's span (errors.problem_response)."""
    current = trace.get_current_span()
    current.set_attribute("gb.problem_code", code)
    if status >= 500:
        current.set_status(StatusCode.ERROR)


def record_error(exc: BaseException) -> None:
    _fail(trace.get_current_span(), exc)


def log_ids(current: Span) -> dict[str, str]:
    """`trace_id` and `span_id` of a sampled span, for a log line; nothing otherwise."""
    context = current.get_span_context()
    if not context.is_valid or not context.trace_flags.sampled:
        return {}
    return {"trace_id": f"{context.trace_id:032x}", "span_id": f"{context.span_id:016x}"}


@contextmanager
def request_span(method: str, path: str) -> Iterator[Span]:
    """The server span of one request, named by its method until `finish_request` knows the
    route template. The liveness check gets none (`UNTRACED_PATHS`)."""
    if path in UNTRACED_PATHS:
        yield trace.INVALID_SPAN  # records nothing, carries no ids
        return
    method = _method(method)
    with span(method, {"http.request.method": method}, SpanKind.SERVER) as current:
        try:
            yield current
        except Exception:
            # It goes on to Starlette's ServerErrorMiddleware, outside this span, whose handler
            # (errors._handle_unexpected) answers `server.error`.
            current.set_attribute("gb.problem_code", "server.error")
            raise


def _method(method: str) -> str:
    return method if method in METHODS else "_OTHER"


def route_template(scope: Mapping[str, Any]) -> str | None:
    """The matched route's full template (`/v1/orders/{order_id}`), None before routing or on a
    404. FastAPI 0.142 includes routers lazily: `scope["route"]` is the route as its own router
    declares it, without the include prefixes (`/health`, not `/v1/health`). The full template is
    the path format of the effective route context it records in `scope["fastapi"]`, which its own
    telemetry names spans with (`_IncludedRouter._handle_selected` in fastapi/routing.py). A route
    on the app itself has no such context and its own path is already full."""
    fastapi_scope = scope.get("fastapi")
    context = (
        fastapi_scope.get("effective_route_context") if isinstance(fastapi_scope, dict) else None
    )
    if context is not None:
        template = getattr(context.starlette_route, "path_format", None) or context.path_format
    else:
        template = getattr(scope.get("route"), "path", None)
    return template if isinstance(template, str) and template else None


def finish_request(current: Span, scope: Mapping[str, Any], status: int, request_id: str) -> None:
    template = route_template(scope)
    if template is not None:
        current.update_name(f"{_method(scope['method'])} {template}")
        current.set_attribute("http.route", template)
    current.set_attribute("http.response.status_code", status)
    current.set_attribute("gb.request_id", request_id)
    if status >= 500:
        current.set_status(StatusCode.ERROR)


# --- outgoing HTTP ------------------------------------------------------------------------------


class TracedTransport(httpx2.AsyncBaseTransport):
    """A client span per request: method, host and status, never the path, query or headers."""

    def __init__(self, inner: httpx2.AsyncBaseTransport | None = None) -> None:
        self._inner = inner or httpx2.AsyncHTTPTransport()

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        host = request.url.host
        attributes = {"http.request.method": request.method, "server.address": host}
        with span(f"{request.method} {host}", attributes, SpanKind.CLIENT) as current:
            if host in _propagate_to:
                _propagator.inject(request.headers)
            response = await self._inner.handle_async_request(request)
            current.set_attribute("http.response.status_code", response.status_code)
            if response.status_code >= 500:
                current.set_status(StatusCode.ERROR)
            return response

    async def aclose(self) -> None:
        await self._inner.aclose()


def http_client(**kwargs: Any) -> httpx2.AsyncClient:
    """An outgoing client with spans; every client the API makes comes from here."""
    return httpx2.AsyncClient(transport=TracedTransport(), **kwargs)


# --- the database -------------------------------------------------------------------------------


def _operation(statement: str) -> str:
    words = statement[:40].split(None, 1)
    return words[0].upper() if words and words[0].isalpha() else "SQL"


def sql_text(statement: str) -> str:
    """The statement with placeholders only: any literal string or number becomes `?`. A
    statement with dollar quoting, an E'' string or a comment is only its keyword."""
    if _SQL_OPAQUE.search(statement):
        return _operation(statement)
    text = " ".join(statement[: SQL_MAX * 2].split())[: SQL_MAX * 2]
    if text.count("'") % 2:  # cut inside a literal: drop the unmatched fragment
        text = text[: text.rfind("'")]
    return _SQL_LITERALS.sub("?", text)[:SQL_MAX]


def _before(_conn: Any, _cursor: Any, statement: str, _params: Any, context: Any, _many: bool):
    if context is None or isinstance(_tracer, trace.NoOpTracer):
        return  # tracing off: no work per statement
    current = _tracer.start_span(
        _operation(statement),
        kind=SpanKind.CLIENT,
        attributes={"db.system.name": "postgresql"},
    )
    if current.is_recording():  # not sampled: the text is not needed
        current.set_attribute("db.query.text", sql_text(statement))
    context.gb_span = current


def _after(_conn: Any, _cursor: Any, _statement: str, _params: Any, context: Any, _many: bool):
    if (current := getattr(context, "gb_span", None)) is not None:
        current.end()


def _error(error: ExceptionContext) -> None:
    if (current := getattr(error.execution_context, "gb_span", None)) is not None:
        _fail(current, error.original_exception)
        sqlstate = getattr(error.original_exception, "sqlstate", None)
        if isinstance(sqlstate, str):  # a code, never the message (it can carry values)
            current.set_attribute("db.response.status_code", sqlstate)
        current.end()


def instrument_engine(engine: Engine) -> None:
    """A span per statement of `engine` (the sync engine behind an AsyncEngine)."""
    event.listen(engine, "before_cursor_execute", _before)
    event.listen(engine, "after_cursor_execute", _after)
    event.listen(engine, "handle_error", _error)


# --- the S3 store (boto3) -----------------------------------------------------------------------


def _s3_start(params: dict[str, Any], context: dict[str, Any], **_: Any) -> None:
    method = str(params.get("method", "GET"))
    host = urlsplit(str(params.get("url", ""))).hostname or "s3"
    context["gb_span"] = _tracer.start_span(
        f"{method} {host}",
        kind=SpanKind.CLIENT,
        attributes={"http.request.method": method, "server.address": host},
    )


def _s3_done(http_response: Any, context: dict[str, Any], **_: Any) -> None:
    if (current := context.pop("gb_span", None)) is not None:
        current.set_attribute("http.response.status_code", http_response.status_code)
        if http_response.status_code >= 500:
            current.set_status(StatusCode.ERROR)
        current.end()


def _s3_failed(exception: BaseException, context: dict[str, Any], **_: Any) -> None:
    if (current := context.pop("gb_span", None)) is not None:
        _fail(current, exception)
        current.end()


def instrument_boto(client: Any) -> None:
    """A client span per call of a boto3 S3 client: method, host and status, never the key."""
    client.meta.events.register("before-call.s3", _s3_start)
    client.meta.events.register("after-call.s3", _s3_done)
    client.meta.events.register("after-call-error.s3", _s3_failed)
