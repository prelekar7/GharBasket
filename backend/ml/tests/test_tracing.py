"""Tracing of the ML service (M0 §6.2): the API's trace continues here, and the never-list holds
on every span of a request that carries a body, a token, a query string and coordinates."""

import logging
import time
from collections.abc import Iterator

import httpx2
import pytest
from fastapi.testclient import TestClient
from gharbasket_ml import registry, tracing
from gharbasket_ml.main import create_app
from gharbasket_ml.settings import Settings
from opentelemetry import metrics, trace
from opentelemetry._logs import get_logger_provider
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.sampling import ALWAYS_OFF
from opentelemetry.trace import SpanContext, SpanKind, StatusCode
from pydantic import SecretStr

TOKEN = "ml-test-token-0123456789abcdef0123456789"
TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
PARENT = f"00-{TRACE_ID}-00f067aa0ba902b7-01"
GRAFANA = "c2VjcmV0LWdyYWZhbmEtdG9rZW4tMDEyMzQ1"


@pytest.fixture
def spans() -> Iterator[InMemorySpanExporter]:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracing.use_provider(provider)
    try:
        yield exporter
    finally:
        tracing.use_provider(None)
        provider.shutdown()


def everything(span: ReadableSpan) -> str:
    events = [(e.name, dict(e.attributes or {})) for e in span.events]
    return f"{span.name} {dict(span.attributes or {})} {events} {span.status.description}"


def ids(span: ReadableSpan) -> SpanContext:
    assert span.context is not None
    return span.context


@pytest.mark.anyio
async def test_a_request_continues_the_apis_trace_and_holds_no_value(
    spans: InMemorySpanExporter,
) -> None:
    app = create_app(Settings(app_env="test", ml_service_token=SecretStr(TOKEN)))
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://ml"
    ) as client:
        answer = await client.get(
            "/models?household=hh-secret-42",
            headers={"Authorization": f"Bearer {TOKEN}", "traceparent": PARENT},
        )
        refused = await client.get(
            "/models", headers={"Authorization": "Bearer wrong-secret", "traceparent": PARENT}
        )
    assert (answer.status_code, refused.status_code) == (200, 401)

    first, second = spans.get_finished_spans()
    assert first.name == "GET /models" and first.kind is SpanKind.SERVER
    assert f"{ids(first).trace_id:032x}" == TRACE_ID  # one trace with the API's
    assert dict(first.attributes or {}) == {
        "http.request.method": "GET",
        "http.route": "/models",
        "http.response.status_code": 200,
    }
    assert (second.attributes or {})["gb.problem_code"] == "ml.unauthorized"
    # Without the token the caller's traceparent is ignored: a new trace, no parent.
    assert f"{ids(second).trace_id:032x}" != TRACE_ID and second.parent is None
    for span in (first, second):
        assert not [v for v in (TOKEN, "hh-secret-42", "wrong-secret") if v in everything(span)]


@pytest.mark.anyio
async def test_outgoing_calls_are_method_host_and_status_only(
    spans: InMemorySpanExporter,
) -> None:
    def osrm(request: httpx2.Request) -> httpx2.Response:
        assert "traceparent" not in request.headers  # nothing goes to a third party
        return httpx2.Response(503)

    inner = httpx2.MockTransport(osrm)
    status = await registry.osrm_status("http://osrm.local:5000", transport=inner)
    with httpx2.Client(transport=tracing.TracedTransport(inner)) as http:
        http.get("https://r2.example.com/gb-mlflow/models/ranker/latest.json?X-Amz-Signature=s")

    assert status == "down"
    osrm_span, r2_span = spans.get_finished_spans()
    assert osrm_span.name == "GET osrm.local" and osrm_span.kind is SpanKind.CLIENT
    assert dict(osrm_span.attributes or {}) == {
        "http.request.method": "GET",
        "server.address": "osrm.local",
        "http.response.status_code": 503,
    }
    assert osrm_span.status.status_code is StatusCode.ERROR
    assert r2_span.name == "GET r2.example.com"
    for span in (osrm_span, r2_span):
        assert not [v for v in ("73.0690", "latest.json", "Signature") if v in everything(span)]


def test_a_failed_call_is_recorded_by_its_type(spans: InMemorySpanExporter) -> None:
    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("no route to osrm.local:5000/table/v1/driving/73.1,19.0")

    with (
        httpx2.Client(transport=tracing.TracedTransport(httpx2.MockTransport(refuse))) as http,
        pytest.raises(httpx2.ConnectError),
    ):
        http.get("http://osrm.local:5000/table/v1/driving/73.1,19.0")
    (span,) = spans.get_finished_spans()
    assert (span.attributes or {})["error.type"] == "ConnectError"
    assert "73.1" not in everything(span)


def test_log_lines_in_a_span_carry_its_ids(spans: InMemorySpanExporter) -> None:
    formatter = tracing.TraceFormatter("%(message)s")
    record = logging.LogRecord("gharbasket_ml", logging.WARNING, __file__, 1, "slow", None, None)
    assert formatter.format(record) == "slow"
    parent = tracing.extract(PARENT)
    with tracing.span("work", SpanKind.INTERNAL, {}, parent) as current:
        line = formatter.format(record)
    context = current.get_span_context()
    assert line == f"slow trace_id={TRACE_ID} span_id={context.span_id:016x}"


def test_tracing_is_off_unless_an_endpoint_is_set_and_export_errors_are_quiet(
    caplog: pytest.LogCaptureFixture,
) -> None:
    assert tracing.setup(Settings(app_env="test")) is None

    class Down:
        calls = 0

        def export(self, spans: object) -> SpanExportResult:
            self.calls += 1
            raise ConnectionError("collector down")

        def shutdown(self) -> None: ...

        def force_flush(self, timeout_millis: int = 0) -> bool:
            return True

    down = Down()
    quiet = tracing.QuietExporter(down)  # pyright: ignore[reportArgumentType]
    with caplog.at_level(logging.WARNING, logger="gharbasket_ml.tracing"):
        assert [quiet.export([]) for _ in range(3)] == [SpanExportResult.FAILURE] * 3
    assert down.calls == 3 and len(caplog.records) == 1
    assert quiet.force_flush() is True
    quiet.shutdown()


def test_an_endpoint_turns_on_a_bounded_batch_export() -> None:
    settings = Settings(
        app_env="staging",
        ml_service_token=SecretStr(TOKEN),
        otel_exporter_otlp_endpoint="http://127.0.0.1:9",
        otel_exporter_otlp_headers=SecretStr("Authorization=Basic%20c2VjcmV0"),
        otel_traces_sampler_arg=0.5,
        GIT_COMMIT="abc1234",  # pyright: ignore[reportCallIssue]
    )
    provider = tracing.setup(settings)
    try:
        assert provider is not None and tracing.setup(settings) is provider
        assert provider.resource.attributes["service.name"] == "gharbasket-ml"
        assert provider.resource.attributes["service.version"] == "abc1234"
        assert provider.resource.attributes["deployment.environment"] == "staging"
    finally:
        if provider is not None:
            provider.shutdown()
        tracing.reset()


def test_the_service_logs_with_trace_ids_unless_the_root_logger_has_a_handler() -> None:
    root = logging.getLogger()
    saved = root.handlers[:]
    root.handlers = []
    try:
        tracing.configure_logging()
        (handler,) = root.handlers
        assert isinstance(handler.formatter, tracing.TraceFormatter)
        tracing.configure_logging()
        assert root.handlers == [handler]
    finally:
        root.handlers = saved


def test_fastapis_own_telemetry_stays_off_with_an_endpoint_in_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:9")
    app = create_app(Settings(app_env="test", otel_exporter_otlp_endpoint=None))
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
    assert not isinstance(trace.get_tracer_provider(), TracerProvider)
    assert not isinstance(metrics.get_meter_provider(), MeterProvider)
    assert not isinstance(get_logger_provider(), LoggerProvider)


def test_renders_health_check_has_no_span_and_nothing_else_is_skipped(
    spans: InMemorySpanExporter,
) -> None:
    app = create_app(Settings(app_env="test"))
    assert {getattr(route, "path", None) for route in app.routes} >= tracing.UNTRACED_PATHS
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        client.get("/health/ai")
        client.get("/healthz")

    names = [span.name for span in spans.get_finished_spans() if span.kind is SpanKind.SERVER]
    assert names == ["GET /health/ai", "GET"]


@pytest.mark.parametrize(
    "headers", [f"Authorization: Basic {GRAFANA}", f'"Authorization=Basic%20{GRAFANA}"']
)
def test_unreadable_headers_leave_export_off_and_are_never_logged(
    headers: str, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", headers)  # the exporter reads it too
    settings = Settings(
        app_env="test",
        otel_exporter_otlp_endpoint="http://127.0.0.1:9",
        otel_exporter_otlp_headers=SecretStr(headers),
    )
    try:
        with caplog.at_level(logging.DEBUG):
            assert tracing.setup(settings) is None
    finally:
        tracing.reset()
    assert "otel_headers_invalid" in caplog.text and GRAFANA not in caplog.text


def test_log_lines_carry_no_ids_from_a_span_that_is_not_sampled() -> None:
    tracing.use_provider(TracerProvider(sampler=ALWAYS_OFF))
    record = logging.LogRecord("gharbasket_ml", logging.WARNING, __file__, 1, "slow", None, None)
    try:
        with tracing.span("work", SpanKind.INTERNAL, {}, None):
            assert tracing.TraceFormatter("%(message)s").format(record) == "slow"
    finally:
        tracing.use_provider(None)


def test_shutdown_waits_5_seconds_at_most_with_the_collector_down() -> None:
    settings = Settings(
        app_env="test",
        otel_exporter_otlp_endpoint="http://127.0.0.1:9",
        otel_exporter_otlp_headers=SecretStr(f"Authorization=Basic%20{GRAFANA}"),
    )
    assert tracing.setup(settings) is not None
    try:
        for n in range(50):
            with tracing.span(f"queued {n}", SpanKind.INTERNAL, {}, None):
                pass
        started = time.monotonic()
        tracing.shutdown(timeout_s=1.0)
        assert time.monotonic() - started < 2.0
    finally:
        tracing.reset()
    tracing.shutdown()  # nothing to stop
