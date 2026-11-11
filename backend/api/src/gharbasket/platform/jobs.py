"""Scheduled jobs without a broker (ADR-0021, M0 §6).

A job is a plain async function registered with `@job("name")`. It runs when something calls
`POST /v1/internal/jobs/<name>` with the operator token: GitHub Actions on a schedule, an external
HTTPS cron for minute-level timing, a person with `just job <name>`, or Taskiq at the pilot.

Every run:
- takes a Postgres advisory lock for the job name, so two triggers at once run it once (the
  second is recorded as "skipped");
- gets the *scheduled* time (`scheduled_for`), which business rules use instead of the clock, so a
  trigger that fires a few minutes late changes nothing;
- is recorded in `platform.job_runs` with its summary or error.

`ops_check` runs the ops probes (platform/ops.py, M0 §6.1); the platform's own probes are here.

Modules register their jobs in their own `jobs.py`; `gharbasket.modules.registry.load_modules()`
imports them all, in the app factory and in every tool (`just job`, `just seed`, `just sim`).
"""

import hashlib
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

import httpx2
import structlog
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from gharbasket.platform import files, ops, outbox
from gharbasket.platform.db import DbSession
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.idempotency import KEY_TTL
from gharbasket.platform.models import IdempotencyKey, JobRun, OutboxEvent
from gharbasket.platform.operator import require_operator
from gharbasket.platform.settings import Settings, get_settings
from gharbasket.platform.valkey import Valkey

log = structlog.get_logger("gharbasket.jobs")
ERROR_LENGTH = 500


@dataclass
class JobContext:
    session: AsyncSession
    scheduled_for: datetime
    settings: Settings
    http: httpx2.AsyncClient | None = None
    valkey: Redis | None = None
    storage: files.ObjectStore | None = None


JobFn = Callable[[JobContext], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class Job:
    name: str
    run: JobFn
    description: str


JOBS: dict[str, Job] = {}


class ProblemsFoundError(Exception):
    """Fail a run with problems written for the operator (`ops_check`). Unlike any other error's
    text, they are kept in the run's summary and sent in the 500 answer, so the alert names them."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


def job(name: str, description: str) -> Callable[[JobFn], JobFn]:
    """Register `fn` as the job `name` (snake_case, unique)."""

    def register(fn: JobFn) -> JobFn:
        if not name.replace("_", "").isalnum() or name != name.lower():
            raise ValueError(f"job names are snake_case, got {name!r}")
        if name in JOBS and JOBS[name].run is not fn:
            raise ValueError(f"job {name!r} is already registered")
        JOBS[name] = Job(name, fn, description)
        return fn

    return register


def lock_key(name: str) -> int:
    """A stable 63-bit key for pg_advisory_xact_lock, from the job name."""
    digest = hashlib.sha256(f"gharbasket:job:{name}".encode()).digest()
    return int.from_bytes(digest[:8], "big") >> 1


def job_unknown(name: str) -> ProblemError:
    return ProblemError(404, "platform.job_unknown", f"No job is called {name!r}.")


async def run_job(
    session: AsyncSession,
    name: str,
    *,
    settings: Settings,
    scheduled_for: datetime | None = None,
    http: httpx2.AsyncClient | None = None,
    valkey: Redis | None = None,
    storage: files.ObjectStore | None = None,
) -> JobRun:
    """Run one job inside `session`'s transaction and commit; the run row is always kept."""
    found = JOBS.get(name)
    if found is None:
        raise job_unknown(name)
    scheduled_for = scheduled_for or datetime.now(UTC)
    request_id = structlog.contextvars.get_contextvars().get("request_id")
    run = JobRun(name=name, scheduled_for=scheduled_for, status="skipped", request_id=request_id)

    locked = (
        await session.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": lock_key(name)}
        )
    ).scalar_one()
    if not locked:
        run.error = "already running"
        run.finished_at = datetime.now(UTC)
        session.add(run)
        await session.commit()
        log.info("job_skipped", job=name, reason="already running")
        return run

    context = JobContext(session, scheduled_for, settings, http, valkey, storage)
    try:
        summary = await found.run(context)
    except Exception as exc:
        await session.rollback()  # the job's partial writes go; the run row stays
        run.status = "failed"
        run.error = f"{type(exc).__name__}: {exc}"[:ERROR_LENGTH]
        if isinstance(exc, ProblemsFoundError):
            run.summary = {"problems": exc.problems}
        run.finished_at = datetime.now(UTC)
        session.add(run)
        await session.commit()
        log.error("job_failed", job=name, error=type(exc).__name__)
        return run
    run.status = "ok"
    run.summary = summary
    run.finished_at = datetime.now(UTC)
    session.add(run)
    await session.commit()
    log.info("job_done", job=name, **summary)
    return run


# --- the platform's own jobs (M0 §6) ----------------------------------------------------------


@job("outbox_sweep", "Deliver outbox events the after-commit relay missed (restart, retry)")
async def outbox_sweep(ctx: JobContext) -> dict[str, Any]:
    relayed = await outbox.relay(ctx.session, limit=500)
    pending = (
        await ctx.session.execute(
            select(func.count()).select_from(OutboxEvent).where(OutboxEvent.sent_at.is_(None))
        )
    ).scalar_one()
    return {"sent": relayed.sent, "failed": relayed.failed, "pending": pending}


@job("cleanup_idempotency_keys", "Delete Idempotency-Key records older than 24 hours")
async def cleanup_idempotency_keys(ctx: JobContext) -> dict[str, Any]:
    deleted = await ctx.session.execute(
        delete(IdempotencyKey)
        .where(IdempotencyKey.created_at < func.now() - KEY_TTL)
        .returning(IdempotencyKey.key)
    )
    return {"deleted": len(deleted.all())}


@job(
    "sweep_uploads",
    "Delete uploads never claimed within 24 hours, and claimed ones past delete_after",
)
async def sweep_uploads(ctx: JobContext) -> dict[str, Any]:
    if ctx.storage is None:
        raise RuntimeError("object storage is not configured (S3_ENDPOINT_URL)")
    return await files.sweep(ctx.session, ctx.storage, now=ctx.scheduled_for)


@job("ops_check", "Run every ops probe; fail, naming each problem, when one finds any (ADR-0033)")
async def ops_check(ctx: JobContext) -> dict[str, Any]:
    problems = await ops.run_probes(ctx.session, ctx.scheduled_for)
    if problems:
        raise ProblemsFoundError(problems)
    return {"probes": len(ops.PROBES), "problems": []}


# --- the platform's probes (M0 §6.1) -----------------------------------------------------------

FAILED_LOOKBACK = timedelta(hours=24)
OUTBOX_STUCK_AFTER = timedelta(minutes=30)
OUTBOX_STUCK_ATTEMPTS = 3
DB_ALERT_SHARE = 0.8


@ops.probe("platform.failed_jobs")
async def failed_jobs(session: AsyncSession, now: datetime) -> list[str]:
    # ops_check is left out: its own failure is the alert, and it would never come back up.
    later = aliased(JobRun)
    healed = (
        select(later.id)
        .where(
            later.name == JobRun.name, later.status == "ok", later.started_at > JobRun.started_at
        )
        .exists()
    )
    rows = await session.execute(
        select(JobRun.name, JobRun.error)
        .where(
            JobRun.status == "failed",
            JobRun.started_at > now - FAILED_LOOKBACK,
            JobRun.name != "ops_check",
            ~healed,
        )
        .order_by(JobRun.name, JobRun.started_at.desc())
        .ext(distinct_on(JobRun.name))
    )
    return [f"{name} failed: {(error or '')[:120]}" for name, error in rows.all()]


@ops.probe("platform.jobs_with_failed_hubs")
async def jobs_with_failed_hubs(session: AsyncSession, now: datetime) -> list[str]:
    """A per-hub job that ended `ok` but listed `failed_hubs` in its summary (a hub that failed
    in its own savepoint while the others stood), in the last 24 h, with no later clean run of the
    same job. The run keeps its `ok` status; this is how the failed hubs reach the `ops` alert.
    The latest such run of each job is named, with its hubs."""
    later = aliased(JobRun)
    healed = (
        select(later.id)
        .where(
            later.name == JobRun.name,
            later.status == "ok",
            later.started_at > JobRun.started_at,
            or_(later.summary.is_(None), ~later.summary.has_key("failed_hubs")),
        )
        .exists()
    )
    rows = await session.execute(
        select(JobRun.name, JobRun.summary, JobRun.started_at)
        .where(
            JobRun.status == "ok",
            JobRun.started_at > now - FAILED_LOOKBACK,
            JobRun.summary.has_key("failed_hubs"),
            ~healed,
        )
        .order_by(JobRun.name, JobRun.started_at.desc())
        .ext(distinct_on(JobRun.name))
    )
    problems: list[str] = []
    for name, summary, started_at in rows.all():
        hubs = (summary or {}).get("failed_hubs")
        if isinstance(hubs, list) and hubs:
            listed = ", ".join(str(hub) for hub in hubs[:10]) + (
                f" and {len(hubs) - 10} more" if len(hubs) > 10 else ""
            )
            at = f"{started_at:%Y-%m-%d %H:%M} UTC"
            problems.append(f"{name} ended ok but hubs failed: {listed} (run of {at})")
    return problems


@ops.probe("platform.outbox_stuck")
async def outbox_stuck(session: AsyncSession, now: datetime) -> list[str]:
    stuck = await session.execute(
        select(OutboxEvent.event_type, func.count())
        .where(
            OutboxEvent.sent_at.is_(None),
            OutboxEvent.created_at < now - OUTBOX_STUCK_AFTER,
            OutboxEvent.attempts >= OUTBOX_STUCK_ATTEMPTS,
        )
        .group_by(OutboxEvent.event_type)
        .order_by(OutboxEvent.event_type)
    )
    counts = stuck.all()
    if not counts:
        return []
    kinds = ", ".join(f"{event_type} x{n}" for event_type, n in counts)
    total = sum(n for _, n in counts)
    return [f"{total} events unsent for > 30 min after 3 or more tries ({kinds})"]


@ops.probe("platform.database_size")
async def database_size(session: AsyncSession, now: datetime) -> list[str]:
    size = (await session.execute(text("SELECT pg_database_size(current_database())"))).scalar_one()
    limit_mb = get_settings().ops_db_limit_mb
    size_mb = size / 2**20
    if size_mb <= DB_ALERT_SHARE * limit_mb:
        return []
    return [f"the database is {size_mb:.0f} MB, {size_mb / limit_mb:.0%} of {limit_mb} MB"]


# --- HTTP ---------------------------------------------------------------------------------------

router = APIRouter(tags=["platform"], dependencies=[Depends(require_operator)])


class JobOut(BaseModel):
    name: str
    description: str


class JobTrigger(BaseModel):
    scheduled_for: datetime | None = Field(
        default=None,
        description="The time this run is for (business rules use it); default: now",
        examples=["2026-11-02T15:30:00Z"],
    )


class JobRunOut(BaseModel):
    id: uuid.UUID
    name: str
    status: str = Field(description="ok, failed or skipped (already running)")
    scheduled_for: datetime
    started_at: datetime
    finished_at: datetime | None
    summary: dict[str, Any] | None
    error: str | None


@router.get("/internal/jobs", summary="The registered jobs (operator token)")
async def list_jobs() -> list[JobOut]:
    return [
        JobOut(name=j.name, description=j.description)
        for j in sorted(JOBS.values(), key=lambda j: j.name)
    ]


@router.get(
    "/internal/jobs/runs",
    summary="Recent job runs, newest first (operator token)",
    responses={401: {"description": "platform.operator_token_required (problem+json)"}},
)
async def list_runs(
    session: DbSession,
    name: Annotated[str | None, Query(max_length=60, description="Only this job")] = None,
    status: Annotated[Literal["ok", "failed", "skipped"] | None, Query()] = None,
    since: Annotated[datetime | None, Query(description="Only runs started after this")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[JobRunOut]:
    query = select(JobRun).order_by(JobRun.started_at.desc(), JobRun.id.desc()).limit(limit)
    if name is not None:
        query = query.where(JobRun.name == name)
    if status is not None:
        query = query.where(JobRun.status == status)
    if since is not None:
        query = query.where(JobRun.started_at > since)
    runs = (await session.execute(query)).scalars().all()
    return [JobRunOut.model_validate(run, from_attributes=True) for run in runs]


@router.post(
    "/internal/jobs/{name}",
    summary="Run a job now (operator token); the trigger for schedules and crons",
    responses={
        401: {"description": "platform.operator_token_required (problem+json)"},
        404: {"description": "platform.job_unknown (problem+json)"},
        500: {
            "description": "platform.job_failed, with the run id, and `problems` for ops_check "
            "(problem+json)"
        },
    },
)
async def trigger_job(
    name: str,
    body: JobTrigger,
    request: Request,
    session: DbSession,
    settings: Annotated[Settings, Depends(get_settings)],
    valkey: Valkey,
) -> JobRunOut:
    run = await run_job(
        session,
        name,
        settings=settings,
        scheduled_for=body.scheduled_for,
        http=getattr(request.app.state, "http", None),
        valkey=valkey,
        storage=getattr(request.app.state, "storage", None),
    )
    if run.status == "failed":
        # A non-2xx answer, so a cron or a person sees it; the run row has the error. Only the
        # problems a job wrote for the operator (ops_check) are sent back.
        extra: dict[str, Any] = {"run_id": str(run.id)}
        if run.summary and "problems" in run.summary:
            extra["problems"] = run.summary["problems"]
        raise ProblemError(500, "platform.job_failed", f"Job {name!r} failed.", extra=extra)
    return JobRunOut.model_validate(run, from_attributes=True)
