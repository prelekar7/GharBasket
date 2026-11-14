"""Ops alerts from the API's side (M0 §6.1, ADR-0033): the job-runs list, the `ops_check` watchdog
and the platform's probes."""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.main import create_app
from gharbasket.platform import jobs, ops
from gharbasket.platform.models import JobRun, OutboxEvent
from gharbasket.platform.settings import use_settings

from .conftest import DEPLOYED_SECRETS, make_settings

pytestmark = [pytest.mark.anyio, pytest.mark.services]

NOW = datetime(2026, 11, 10, 18, 0, tzinfo=UTC)


def run_row(
    name: str,
    status: str,
    hours_ago: float,
    error: str | None = None,
    summary: dict[str, Any] | None = None,
) -> JobRun:
    at = NOW - timedelta(hours=hours_ago)
    return JobRun(
        name=name,
        status=status,
        scheduled_for=at,
        started_at=at,
        finished_at=at,
        error=error,
        summary=summary,
    )


# --- GET /v1/internal/jobs/runs ----------------------------------------------------------------


def ours(answer: httpx2.Response) -> list[str]:
    """The names of this test's runs: the shared test database may hold others (the seed's)."""
    return [run["name"] for run in answer.json() if run["name"].startswith("test_")]


async def test_runs_are_listed_newest_first_and_filtered(
    api: httpx2.AsyncClient, db_session: AsyncSession
) -> None:
    db_session.add_all(
        [
            run_row("test_cut", "ok", 30),
            run_row("test_cut", "failed", 2, "RuntimeError: boom"),
            run_row("test_other", "ok", 1),
        ]
    )
    await db_session.flush()

    every = await api.get(
        "/v1/internal/jobs/runs", params={"since": (NOW - timedelta(days=3)).isoformat()}
    )
    cut = await api.get("/v1/internal/jobs/runs", params={"name": "test_cut"})
    failed = await api.get(
        "/v1/internal/jobs/runs", params={"name": "test_cut", "status": "failed"}
    )
    recent = await api.get(
        "/v1/internal/jobs/runs", params={"since": (NOW - timedelta(hours=3)).isoformat()}
    )
    one = await api.get("/v1/internal/jobs/runs", params={"limit": 1})
    bad = await api.get("/v1/internal/jobs/runs", params={"status": "maybe", "limit": 500})

    assert ours(every) == ["test_other", "test_cut", "test_cut"]
    assert [r["status"] for r in cut.json()] == ["failed", "ok"]
    assert [(r["status"], r["error"]) for r in failed.json()] == [("failed", "RuntimeError: boom")]
    assert ours(recent) == ["test_other", "test_cut"]
    assert len(one.json()) == 1
    assert bad.status_code == 422


def test_runs_need_the_operator_token_when_deployed() -> None:
    settings = make_settings(app_env="staging", **DEPLOYED_SECRETS)
    with TestClient(create_app(settings)) as client:
        missing = client.get("/v1/internal/jobs/runs")

    assert missing.status_code == 401
    assert missing.json()["code"] == "platform.operator_token_required"


# --- ops_check -----------------------------------------------------------------------------------


async def test_ops_check_fails_naming_every_problem_and_a_probe_that_raises(
    api: httpx2.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def backlog(session: AsyncSession, now: datetime) -> list[str]:
        return ["3 webhooks unprocessed for > 15 min"]

    async def broken(session: AsyncSession, now: datetime) -> list[str]:
        raise RuntimeError("database password is hunter2")

    async def clean(session: AsyncSession, now: datetime) -> list[str]:
        return []

    probes = {"test.backlog": backlog, "test.broken": broken, "test.clean": clean}
    monkeypatch.setattr(ops, "PROBES", probes)

    answer = await api.post("/v1/internal/jobs/ops_check", json={"scheduled_for": NOW.isoformat()})

    body = answer.json()
    assert (answer.status_code, body["code"]) == (500, "platform.job_failed")
    assert body["problems"] == [
        "test.backlog: 3 webhooks unprocessed for > 15 min",
        "test.broken: probe failed RuntimeError",
    ]
    assert "hunter2" not in answer.text  # a probe's exception text stays out of the answer
    runs = await api.get("/v1/internal/jobs/runs", params={"name": "ops_check"})
    assert runs.json()[0]["summary"] == {"problems": body["problems"]}
    assert runs.json()[0]["error"].startswith("ProblemsFoundError: test.backlog: 3 webhooks")


async def test_ops_check_is_ok_when_no_probe_finds_anything(
    api: httpx2.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def clean(session: AsyncSession, now: datetime) -> list[str]:
        return []

    monkeypatch.setattr(ops, "PROBES", {"test.clean": clean})

    answer = await api.post("/v1/internal/jobs/ops_check", json={"scheduled_for": NOW.isoformat()})

    assert answer.status_code == 200
    assert (answer.json()["status"], answer.json()["summary"]) == (
        "ok",
        {"probes": 1, "problems": []},
    )


def test_probe_names_are_module_dot_name_and_unique() -> None:
    def register() -> None:
        @ops.probe("Bad.Name")
        async def bad(session: AsyncSession, now: datetime) -> list[str]:
            return []

    with pytest.raises(ValueError, match="snake_case"):
        register()
    with pytest.raises(ValueError, match="already registered"):
        ops.probe("platform.failed_jobs")(jobs.outbox_stuck)


def test_the_platform_probes_are_registered() -> None:
    assert {
        "platform.failed_jobs",
        "platform.jobs_with_failed_hubs",
        "platform.jobs_with_failed_households",
        "platform.outbox_stuck",
        "platform.database_size",
    } <= set(ops.PROBES)


# --- the platform's probes -----------------------------------------------------------------------


async def test_a_failure_is_reported_until_a_later_run_is_ok(db_session: AsyncSession) -> None:
    db_session.add_all(
        [
            run_row("test_healed", "failed", 5, "RuntimeError: once"),
            run_row("test_healed", "ok", 4),
            run_row("test_broken", "ok", 6),
            run_row("test_broken", "failed", 3, "RuntimeError: first"),
            run_row("test_broken", "failed", 1, "TimeoutError: " + "x" * 300),
            run_row("test_old", "failed", 30, "RuntimeError: yesterday"),
            run_row("ops_check", "failed", 1, "ProblemsFoundError: itself"),
        ]
    )
    await db_session.flush()

    found = await jobs.failed_jobs(db_session, NOW)

    assert found == ["test_broken failed: TimeoutError: " + "x" * 106]


async def test_an_ok_run_with_failed_hubs_is_reported_until_a_later_run_is_clean(
    db_session: AsyncSession,
) -> None:
    db_session.add_all(
        [
            run_row("test_healed", "ok", 5, summary={"runs": 1, "failed_hubs": ["hub-a"]}),
            run_row("test_healed", "ok", 4, summary={"runs": 2}),
            run_row("test_skipped_after", "ok", 5, summary={"failed_hubs": ["hub-z"]}),
            run_row("test_skipped_after", "skipped", 4, error="already running"),
            run_row("test_clean", "ok", 3, summary={"runs": 3}),
            run_row("test_nosummary", "ok", 3),
            run_row("test_old", "ok", 30, summary={"failed_hubs": ["hub-old"]}),
            run_row("test_broken", "ok", 6, summary={"failed_hubs": ["hub-a"]}),
            run_row("test_broken", "ok", 2, summary={"failed_hubs": ["hub-b", "hub-c"]}),
            run_row("test_failed_run", "failed", 1, "RuntimeError: x", {"failed_hubs": ["hub-f"]}),
        ]
    )
    await db_session.flush()

    found = await jobs.jobs_with_failed_hubs(db_session, NOW)

    assert found == [
        "test_broken ended ok but hubs failed: hub-b, hub-c (run of 2026-11-10 16:00 UTC)",
        "test_skipped_after ended ok but hubs failed: hub-z (run of 2026-11-10 13:00 UTC)",
    ]


async def test_an_ok_run_with_failed_households_is_reported_until_a_later_run_is_clean(
    db_session: AsyncSession,
) -> None:
    db_session.add_all(
        [
            run_row("test_healed", "ok", 5, summary={"failed_households": ["hh-a"]}),
            run_row("test_healed", "ok", 4, summary={"created": 2}),
            # a later run that has no failed_households heals it, though it lists failed_hubs
            run_row("test_other_key", "ok", 5, summary={"failed_households": ["hh-k"]}),
            run_row("test_other_key", "ok", 4, summary={"failed_hubs": ["hub-a"]}),
            run_row("test_skipped_after", "ok", 5, summary={"failed_households": ["hh-z"]}),
            run_row("test_skipped_after", "skipped", 4, error="already running"),
            run_row("test_old", "ok", 30, summary={"failed_households": ["hh-old"]}),
            run_row(
                "test_failed_run", "failed", 1, "RuntimeError: x", {"failed_households": ["f"]}
            ),
            run_row("test_empty", "ok", 3, summary={"failed_households": []}),
            run_row("test_clean", "ok", 3, summary={"created": 1}),
            run_row(
                "test_many", "ok", 2, summary={"failed_households": [f"hh-{n}" for n in range(12)]}
            ),
        ]
    )
    await db_session.flush()

    found = await jobs.jobs_with_failed_households(db_session, NOW)
    hubs = await jobs.jobs_with_failed_hubs(db_session, NOW)

    ids = ", ".join(f"hh-{n}" for n in range(10))
    assert found == [
        f"test_many ended ok but households failed: {ids} and 2 more (run of 2026-11-10 16:00 UTC)",
        "test_skipped_after ended ok but households failed: hh-z (run of 2026-11-10 13:00 UTC)",
    ]
    assert hubs == ["test_other_key ended ok but hubs failed: hub-a (run of 2026-11-10 14:00 UTC)"]


async def test_stuck_outbox_events_are_counted_by_type(db_session: AsyncSession) -> None:
    old = NOW - timedelta(hours=1)
    db_session.add_all(
        [
            OutboxEvent(event_type="test.stuck", payload={}, created_at=old, attempts=3),
            OutboxEvent(event_type="test.stuck", payload={}, created_at=old, attempts=7),
            OutboxEvent(event_type="test.retrying", payload={}, created_at=old, attempts=2),
            OutboxEvent(event_type="test.fresh", payload={}, created_at=NOW, attempts=5),
            OutboxEvent(
                event_type="test.sent", payload={}, created_at=old, attempts=4, sent_at=NOW
            ),
        ]
    )
    await db_session.flush()

    found = await jobs.outbox_stuck(db_session, NOW)

    assert found == ["2 events unsent for > 30 min after 3 or more tries (test.stuck x2)"]


async def test_the_database_size_is_reported_above_80_percent_of_the_limit(
    db_session: AsyncSession,
) -> None:
    assert await jobs.database_size(db_session, NOW) == []  # a test database is far below 512 MB

    use_settings(make_settings(ops_db_limit_mb=1))
    found = await jobs.database_size(db_session, NOW)

    assert len(found) == 1 and found[0].startswith("the database is ")
    assert found[0].endswith(" of 1 MB")
