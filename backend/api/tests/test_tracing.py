"""Tracing (M0 §6.2): what becomes a span, and the never-list, asserted on the spans of a request
that carries every kind of value a span must not hold."""

import logging
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx2
import pytest
import structlog
from fastapi import FastAPI
from fastapi.testclient import TestClient
from opentelemetry import trace
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExportResult
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanContext, SpanKind, StatusCode
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.main import create_app
from gharbasket.platform import tracing
from gharbasket.platform.db import DbSession, get_session
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.jobs import JOBS, JobContext, job, run_job
from gharbasket.platform.logging import add_trace_ids
from gharbasket.platform.models import OutboxEvent
from gharbasket.platform.outbox import CONSUMERS, publish, relay, subscribe

from .commit_guard import CommitGuard
from .conftest import add_test_routes, make_settings

ITEM = uuid.UUID("0199a1b2-3c4d-7e5f-8a9b-0c1d2e3f4a5b")
PHONE = "+919876543210"
BEARER = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ0cmFjZS10ZXN0In0.c2lnbmF0dXJlLXNlY3JldA"  # gitleaks:allow
COOKIE = "gb_session=cookie-secret-4f2a"
IDEMPOTENCY_KEY = "idem-secret-8c1d"
QUERY_SECRET = "query-secret-77ab"
ML_TOKEN = "ml-token-secret-0123456789abcdef0123456789"
NEVER = [
    str(ITEM),
    PHONE,
    "9876543210",
    BEARER,
    "cookie-secret",
    IDEMPOTENCY_KEY,
    QUERY_SECRET,
    ML_TOKEN,
    "kitchen-secret-path",
    "hunter2",
]


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
    """All a span holds that is exported: name, attributes, events and status."""
    events = [(e.name, dict(e.attributes or {})) for e in span.events]
    return f"{span.name} {dict(span.attributes or {})} {events} {span.status.description}"


def ids(span: ReadableSpan) -> SpanContext:
    assert span.context is not None
    return span.context


def traced_app(seen: list[httpx2.Request]) -> FastAPI:
    """The app, with a route that does what the never-list is about: reads a phone from the body,
    queries with parameters, calls the ML service and another provider, and raises a problem."""
    app = create_app(make_settings(ml_service_url="http://ml.internal:8100"))
    add_test_routes(app)

    def answer(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={"ok": True})

    class Caller(BaseModel):
        phone: str

    @app.post("/v1/_test/trace/{item_id}")
    async def trace_me(item_id: uuid.UUID, session: DbSession, body: Caller) -> None:
        phone = body.phone
        await session.execute(
            text("SELECT CAST(:phone AS text) || CAST(:item AS text) || 'kitchen-secret-path'"),
            {"phone": phone, "item": str(item_id)},
        )
        transport = tracing.TracedTransport(httpx2.MockTransport(answer))
        async with httpx2.AsyncClient(transport=transport) as http:
            await http.post(
                f"http://ml.internal:8100/plan/{item_id}?token={QUERY_SECRET}",
                headers={"Authorization": f"Bearer {ML_TOKEN}"},
                json={"phone": phone},
            )
            await http.get(f"https://api.razorpay.com/v1/orders/{item_id}?phone={phone}")
        structlog.get_logger("gharbasket.test").info("traced", phone=phone)
        raise ProblemError(409, "orders.cutoff_passed", f"Not for {phone} ({item_id}).")

    return app


@pytest.fixture
async def traced(
    commit_guard: CommitGuard, spans: InMemorySpanExporter
) -> AsyncIterator[tuple[httpx2.AsyncClient, list[httpx2.Request]]]:
    seen: list[httpx2.Request] = []
    app = traced_app(seen)
    app.dependency_overrides[get_session] = commit_guard.override()
    transport = httpx2.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, seen


@pytest.mark.anyio
@pytest.mark.services
async def test_no_span_holds_a_body_header_query_path_parameter_id_or_message(
    traced: tuple[httpx2.AsyncClient, list[httpx2.Request]], spans: InMemorySpanExporter
) -> None:
    client, seen = traced
    answer = await client.post(
        f"/v1/_test/trace/{ITEM}?token={QUERY_SECRET}&phone={PHONE}",
        headers={
            "Authorization": f"Bearer {BEARER}",
            "Cookie": COOKIE,
            "Idempotency-Key": IDEMPOTENCY_KEY,
        },
        json={"phone": PHONE},
    )
    assert answer.status_code == 409

    finished = spans.get_finished_spans()
    for span in finished:
        held = everything(span)
        assert not [value for value in NEVER if value in held], span.name
    by_name = {span.name: span for span in finished}
    server = by_name["POST /v1/_test/trace/{item_id}"]
    assert server.kind is SpanKind.SERVER
    assert dict(server.attributes or {}) == {
        "http.request.method": "POST",
        "http.route": "/v1/_test/trace/{item_id}",
        "http.response.status_code": 409,
        "gb.problem_code": "orders.cutoff_passed",
        "gb.request_id": answer.headers["x-request-id"],
    }
    queries = [s for s in finished if (s.attributes or {}).get("db.system.name") == "postgresql"]
    assert any("CAST($1" in str((s.attributes or {})["db.query.text"]) for s in queries)
    ml, razorpay = by_name["POST ml.internal"], by_name["GET api.razorpay.com"]
    assert dict(ml.attributes or {}) == {
        "http.request.method": "POST",
        "server.address": "ml.internal",
        "http.response.status_code": 200,
    }
    # One trace: every span under the request's, and the ML service gets its context.
    assert {ids(s).trace_id for s in finished} == {ids(server).trace_id}
    assert {s.parent.span_id for s in (ml, razorpay, *queries) if s.parent} == {ids(server).span_id}
    to_ml, to_razorpay = seen
    assert to_ml.headers["traceparent"].split("-")[1] == f"{ids(server).trace_id:032x}"
    assert "traceparent" not in to_razorpay.headers


@pytest.mark.anyio
@pytest.mark.services
async def test_an_unexpected_error_is_recorded_by_its_type_only(
    traced: tuple[httpx2.AsyncClient, list[httpx2.Request]], spans: InMemorySpanExporter
) -> None:
    client, _ = traced
    answer = await client.get("/v1/_test/boom")  # RuntimeError("database password is hunter2")
    assert answer.status_code == 500

    (server,) = [s for s in spans.get_finished_spans() if s.kind is SpanKind.SERVER]
    assert server.name == "GET /v1/_test/boom"
    assert server.status.status_code is StatusCode.ERROR
    assert (server.attributes or {})["error.type"] == "RuntimeError"
    assert (server.attributes or {})["gb.problem_code"] == "server.error"
    assert "hunter2" not in everything(server)


@pytest.mark.anyio
@pytest.mark.services
async def test_job_runs_and_consumers_are_spans(
    db_session: AsyncSession, spans: InMemorySpanExporter
) -> None:
    @job("test_traced", "a job with a span")
    async def traced_job(ctx: JobContext) -> dict[str, Any]:
        await publish(ctx.session, "test.traced", {"phone": PHONE})
        await relay(ctx.session)
        return {"done": 1}

    async def consumer(session: AsyncSession, event: OutboxEvent) -> None:
        raise ValueError(f"cannot handle {event.payload['phone']}")

    subscribe("test.traced", consumer)
    try:
        run = await run_job(db_session, "test_traced", settings=make_settings())
    finally:
        JOBS.pop("test_traced")
        CONSUMERS.pop("test.traced")

    by_name = {span.name: span for span in spans.get_finished_spans()}
    ran, consumed = by_name["job test_traced"], by_name["consume test.traced"]
    assert dict(ran.attributes or {}) == {
        "gb.job.scheduled_for": run.scheduled_for.isoformat(),
        "gb.job.status": "ok",
        "gb.job.run_id": str(run.id),
    }
    assert dict(consumed.attributes or {}) == {
        "gb.consumer": f"{consumer.__module__}.{consumer.__qualname__}",
        "gb.attempt": 1,
        "error.type": "ValueError",
    }
    assert consumed.parent is not None and consumed.parent.span_id == ids(ran).span_id
    assert PHONE not in everything(consumed)


def test_an_s3_call_is_method_host_and_status_only(spans: InMemorySpanExporter) -> None:
    import boto3
    from botocore.awsrequest import AWSResponse
    from botocore.config import Config

    class Raw:
        def stream(self, **_: Any) -> Iterator[bytes]:
            yield b""

    statuses = iter((200, 404))

    def store(**_: Any) -> AWSResponse:  # answers in place of the network, after signing
        return AWSResponse("https://r2.example.com", next(statuses), {}, Raw())

    s3 = boto3.client(
        "s3",
        endpoint_url="https://r2.example.com",
        aws_access_key_id="key-id",
        aws_secret_access_key="s3-secret-0123456789",  # gitleaks:allow
        region_name="auto",
        config=Config(s3={"addressing_style": "path"}),  # as files.create_store
    )
    tracing.instrument_boto(s3)
    key = f"kyc_document/{ITEM}/aadhaar-free.jpg"
    s3.meta.events.register("before-send.s3", store)
    s3.head_object(Bucket="gb-private", Key=key)
    with pytest.raises(s3.exceptions.ClientError):
        s3.head_object(Bucket="gb-private", Key=key)

    found, missing = spans.get_finished_spans()
    assert found.name == "HEAD r2.example.com" and found.kind is SpanKind.CLIENT
    assert dict(found.attributes or {}) == {
        "http.request.method": "HEAD",
        "server.address": "r2.example.com",
        "http.response.status_code": 200,
    }
    assert (missing.attributes or {})["http.response.status_code"] == 404
    for span in (found, missing):
        assert not [v for v in (str(ITEM), "gb-private", "s3-secret") if v in everything(span)]


def test_fastapis_own_telemetry_stays_off_with_an_endpoint_in_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # FastAPI would add OTLP exporters (traces, metrics, logs) to the global providers itself.
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:9")
    app = create_app(make_settings(otel_exporter_otlp_endpoint=None))
    with TestClient(app) as client:
        assert client.get("/v1/health").status_code == 200
    assert not isinstance(trace.get_tracer_provider(), TracerProvider)


def test_log_lines_in_a_span_carry_its_ids(spans: InMemorySpanExporter) -> None:
    assert add_trace_ids(None, "info", {"event": "outside"}) == {"event": "outside"}
    with tracing.span("work") as current:
        line = add_trace_ids(None, "info", {"event": "inside"})
    context = current.get_span_context()
    assert line == {
        "event": "inside",
        "trace_id": f"{context.trace_id:032x}",
        "span_id": f"{context.span_id:016x}",
    }


def test_sql_text_keeps_placeholders_and_drops_literals() -> None:
    sql = "SELECT a1, $1, 'x''y' FROM t2 WHERE n = 42 AND p = '+919876543210' LIMIT $2"
    assert tracing.sql_text(sql) == "SELECT a1, $1, ? FROM t2 WHERE n = ? AND p = ? LIMIT $2"


def test_tracing_is_off_unless_an_endpoint_is_set_and_export_errors_are_quiet(
    caplog: pytest.LogCaptureFixture,
) -> None:
    assert tracing.setup(make_settings()) is None

    class Down:
        calls = 0

        def export(self, spans: object) -> SpanExportResult:
            self.calls += 1
            raise ConnectionError("collector at https://user:secret@otlp down")

        def shutdown(self) -> None: ...

        def force_flush(self, timeout_millis: int = 0) -> bool:
            return True

    down = Down()
    quiet = tracing.QuietExporter(down)  # pyright: ignore[reportArgumentType]
    with structlog.testing.capture_logs() as logs, caplog.at_level(logging.DEBUG):
        results = [quiet.export([]) for _ in range(3)]
    assert results == [SpanExportResult.FAILURE] * 3 and down.calls == 3
    assert [(e["event"], e["reason"]) for e in logs] == [("trace_export_failed", "ConnectionError")]
    assert "secret" not in caplog.text


def test_an_endpoint_turns_on_a_bounded_batch_export() -> None:
    settings = make_settings(
        otel_exporter_otlp_endpoint="http://127.0.0.1:9",
        otel_exporter_otlp_headers="Authorization=Basic%20c2VjcmV0",
        otel_traces_sampler_arg=0.25,
        GIT_COMMIT="abc1234",
    )
    provider = tracing.setup(settings)
    try:
        assert provider is not None and tracing.setup(settings) is provider  # once per process
        assert provider.resource.attributes["service.name"] == "gharbasket-api"
        assert provider.resource.attributes["service.version"] == "abc1234"
        assert provider.resource.attributes["deployment.environment"] == "test"
        assert "0.25" in provider.sampler.get_description()
    finally:
        provider.shutdown() if provider else None
        tracing.reset()
