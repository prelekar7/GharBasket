"""Scheduled jobs and outbox delivery (M0 §6, ADR-0021)."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.main import create_app
from gharbasket.platform import outbox
from gharbasket.platform.db import DbSession, create_engine
from gharbasket.platform.jobs import JOBS, JobContext, job, lock_key, run_job
from gharbasket.platform.models import IdempotencyKey, JobRun, OutboxEvent
from gharbasket.platform.outbox import publish, relay, subscribe
from gharbasket.tools import run_job as run_job_tool

from .conftest import DEPLOYED_SECRETS, make_settings

pytestmark = [pytest.mark.anyio, pytest.mark.services]


def test_job_names_are_snake_case_and_unique() -> None:
    with pytest.raises(ValueError, match="snake_case"):
        job("Bad-Name", "x")(lambda ctx: asyncio.sleep(0))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="already registered"):
        job("outbox_sweep", "x")(lambda ctx: asyncio.sleep(0))  # type: ignore[arg-type]
    assert 0 < lock_key("outbox_sweep") < 2**63
    assert lock_key("outbox_sweep") != lock_key("cleanup_idempotency_keys")


async def test_the_registered_jobs_are_listed_and_unknown_ones_are_404(
    api: httpx2.AsyncClient,
) -> None:
    listed = await api.get("/v1/internal/jobs")
    unknown = await api.post("/v1/internal/jobs/no_such_job", json={})

    assert [j["name"] for j in listed.json()] == sorted(JOBS)
    assert {"cleanup_idempotency_keys", "outbox_sweep"} <= set(JOBS)
    assert unknown.status_code == 404
    assert unknown.json()["code"] == "platform.job_unknown"


def test_jobs_need_the_operator_token_when_deployed() -> None:
    settings = make_settings(app_env="staging", **DEPLOYED_SECRETS)
    with TestClient(create_app(settings)) as client:
        missing = client.post("/v1/internal/jobs/outbox_sweep", json={})
        listed = client.get("/v1/internal/jobs", headers={"Authorization": "Bearer op-token"})

    assert missing.status_code == 401
    assert missing.json()["code"] == "platform.operator_token_required"
    assert listed.status_code == 200


async def test_cleanup_deletes_only_expired_keys_and_records_the_run(
    api: httpx2.AsyncClient, db_session: AsyncSession
) -> None:
    db_session.add_all(
        [
            IdempotencyKey(scope="s", key="old", request_hash="h", status_code=201),
            IdempotencyKey(scope="s", key="fresh", request_hash="h", status_code=201),
        ]
    )
    await db_session.flush()
    await db_session.execute(
        update(IdempotencyKey)
        .where(IdempotencyKey.key == "old")
        .values(created_at=func.now() - text("interval '25 hours'"))
    )
    when = "2026-11-02T22:03:00Z"

    answer = await api.post(
        "/v1/internal/jobs/cleanup_idempotency_keys", json={"scheduled_for": when}
    )

    body = answer.json()
    assert (answer.status_code, body["status"], body["summary"]) == (200, "ok", {"deleted": 1})
    assert body["scheduled_for"].startswith("2026-11-02T22:03:00")
    left = (await db_session.execute(select(IdempotencyKey.key))).scalars().all()
    assert left == ["fresh"]
    run = await db_session.get(JobRun, uuid.UUID(body["id"]))
    assert run is not None and run.status == "ok" and run.finished_at is not None
    assert run.request_id == answer.headers["x-request-id"]


async def test_a_failing_job_keeps_its_run_row_and_answers_500(
    api: httpx2.AsyncClient, db_session: AsyncSession
) -> None:
    @job("test_boom", "fails after writing")
    async def boom(ctx: JobContext) -> dict[str, Any]:
        await publish(ctx.session, "orders.locked", {"from": "boom"})
        raise RuntimeError("database password is hunter2")

    try:
        answer = await api.post("/v1/internal/jobs/test_boom", json={})
    finally:
        JOBS.pop("test_boom")

    assert answer.status_code == 500
    assert answer.json()["code"] == "platform.job_failed"
    run = await db_session.get(JobRun, uuid.UUID(answer.json()["run_id"]))
    assert run is not None
    assert (run.status, run.error) == ("failed", "RuntimeError: database password is hunter2")
    written = await db_session.execute(
        select(func.count()).where(OutboxEvent.payload["from"].astext == "boom")
    )
    assert written.scalar_one() == 0  # the job's own writes were rolled back


async def test_two_triggers_at_once_run_the_job_once(database_url: str) -> None:
    """Real connections: the advisory lock makes the second trigger a 'skipped' run."""
    settings = make_settings()
    engine = create_engine(database_url, pooled=False)
    marker = uuid.uuid4().hex

    @job("test_slow", "holds the lock for a moment")
    async def slow(ctx: JobContext) -> dict[str, Any]:
        await asyncio.sleep(0.3)
        return {"marker": marker}

    async def trigger() -> JobRun:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            return await run_job(session, "test_slow", settings=settings)

    try:
        first, second = await asyncio.gather(trigger(), trigger())
        assert sorted([first.status, second.status]) == ["ok", "skipped"]
        skipped = first if first.status == "skipped" else second
        assert skipped.error == "already running"
    finally:
        JOBS.pop("test_slow")
        async with AsyncSession(engine) as session:
            await session.execute(delete(JobRun).where(JobRun.name == "test_slow"))
            await session.commit()
        await engine.dispose()


# --- outbox delivery --------------------------------------------------------------------------


async def test_relay_delivers_to_consumers_and_retries_failures_with_backoff(
    db_session: AsyncSession,
) -> None:
    seen: list[str] = []
    calls = {"n": 0}

    async def remember(session: AsyncSession, event: OutboxEvent) -> None:
        seen.append(event.payload["id"])

    async def flaky(session: AsyncSession, event: OutboxEvent) -> None:
        calls["n"] += 1
        await publish(session, "notifications.sent", {"for": event.payload["id"]})
        if calls["n"] == 1:
            raise RuntimeError("consumer down")

    subscribe("orders.locked", remember)
    subscribe("orders.delivered", flaky)
    try:
        await publish(db_session, "orders.locked", {"id": "a"})
        bad = await publish(db_session, "orders.delivered", {"id": "b"})
        await publish(db_session, "identity.nobody_listens", {"id": "c"})

        first = await relay(db_session)
        event = await db_session.get(OutboxEvent, bad)
        assert event is not None
        assert (first.sent, first.failed, seen) == (2, 1, ["a"])
        assert event.sent_at is None and event.attempts == 1
        assert event.last_error == "RuntimeError: consumer down"
        assert event.available_at > datetime.now(UTC) + timedelta(seconds=20)
        # The failed handler's own write was rolled back with the savepoint.
        side = await db_session.execute(
            select(func.count()).where(OutboxEvent.event_type == "notifications.sent")
        )
        assert side.scalar_one() == 0

        not_yet = await relay(db_session)  # still backing off
        assert (not_yet.sent, not_yet.failed) == (0, 0)
        event.available_at = datetime.now(UTC) - timedelta(seconds=1)
        await db_session.flush()
        retried = await relay(db_session)
        assert (retried.sent, retried.failed, calls["n"]) == (1, 0, 2)
        side = await db_session.execute(
            select(func.count()).where(OutboxEvent.event_type == "notifications.sent")
        )
        assert side.scalar_one() == 1
    finally:
        outbox.CONSUMERS["orders.locked"].remove(remember)
        outbox.CONSUMERS["orders.delivered"].remove(flaky)


def test_backoff_grows_and_is_capped() -> None:
    assert [outbox.backoff(n).total_seconds() for n in (1, 2, 3, 4)] == [30, 60, 120, 240]
    assert outbox.backoff(20) == timedelta(hours=1)
    with pytest.raises(ValueError, match=r"module\.event"):
        subscribe("bad name", lambda s, e: asyncio.sleep(0))  # type: ignore[arg-type]


async def test_a_request_that_publishes_gets_its_event_relayed_after_commit(
    database_url: str,
) -> None:
    """A real app with its lifespan: the after-commit relay delivers within a moment."""
    delivered: list[str] = []
    marker = uuid.uuid4().hex

    async def on_locked(session: AsyncSession, event: OutboxEvent) -> None:
        delivered.append(event.payload["marker"])

    subscribe("orders.locked", on_locked)
    app = create_app(make_settings(database_url=database_url))

    @app.post("/v1/_test/lock", status_code=201)
    async def lock(session: DbSession) -> dict[str, str]:
        event_id = await publish(session, "orders.locked", {"marker": marker})
        await session.commit()
        return {"event_id": str(event_id)}

    try:
        async with app.router.lifespan_context(app):
            transport = httpx2.ASGITransport(app=app)
            async with httpx2.AsyncClient(transport=transport, base_url="http://test") as client:
                answer = await client.post("/v1/_test/lock")
                assert answer.status_code == 201
                await outbox.drain()
        assert delivered == [marker]
    finally:
        outbox.CONSUMERS["orders.locked"].remove(on_locked)
        engine = create_engine(database_url, pooled=False)
        async with AsyncSession(engine) as session:
            mine = OutboxEvent.payload["marker"].astext == marker
            event = (await session.execute(select(OutboxEvent).where(mine))).scalar_one()
            assert event.sent_at is not None
            await session.execute(delete(OutboxEvent).where(mine))
            await session.commit()
        await engine.dispose()


async def test_the_sweep_job_reports_what_is_left(
    api: httpx2.AsyncClient, db_session: AsyncSession
) -> None:
    await publish(db_session, "orders.locked", {"id": "x"})
    await db_session.execute(
        update(OutboxEvent).values(available_at=func.now() + text("interval '1 hour'"))
    )

    answer = await api.post("/v1/internal/jobs/outbox_sweep", json={})

    assert answer.json()["summary"] == {"sent": 0, "failed": 0, "pending": 1}


# --- the local tool ---------------------------------------------------------------------------


def test_the_tool_refuses_a_shared_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL_DIRECT", "postgresql+asyncpg://u:p@ep-x.neon.tech/gharbasket")
    with pytest.raises(SystemExit):
        run_job_tool.main(["outbox_sweep"])
    monkeypatch.delenv("DATABASE_URL_DIRECT")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost:5432/x")
    with pytest.raises(SystemExit):
        run_job_tool.main(["no_such_job"])


def test_the_tool_runs_a_job_on_the_local_database(
    database_url: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("DATABASE_URL_DIRECT", database_url)

    code = run_job_tool.main(["cleanup_idempotency_keys", "--scheduled-for", "2026-11-02T22:03"])

    assert code == 0
    assert '"status": "ok"' in capsys.readouterr().out


@pytest.fixture(autouse=True)
def _no_leftover_jobs() -> Any:
    yield
    assert "test_boom" not in JOBS and "test_slow" not in JOBS
