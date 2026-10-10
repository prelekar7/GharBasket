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

Modules register their jobs in their own `jobs.py`, imported by their router so the registry is
complete once the app is built.
"""

import hashlib
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated, Any

import httpx2
import structlog
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.platform import files, outbox
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


@router.post(
    "/internal/jobs/{name}",
    summary="Run a job now (operator token); the trigger for schedules and crons",
    responses={
        401: {"description": "platform.operator_token_required (problem+json)"},
        404: {"description": "platform.job_unknown (problem+json)"},
        500: {"description": "platform.job_failed, with the run id (problem+json)"},
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
        # A non-2xx answer, so a cron or a person sees it; the run row has the error.
        raise ProblemError(
            500, "platform.job_failed", f"Job {name!r} failed.", extra={"run_id": str(run.id)}
        )
    return JobRunOut.model_validate(run, from_attributes=True)
