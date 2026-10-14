"""Jobs of M7 Logistics (spec §6, ADR-0021): `solve_routes` at T-1 23:15 IST; `persist_gps_pings`
every minute, `close_routes` at T 16:00 and `purge_gps_pings` daily (90 days).

`scheduled_for` decides the delivery date (the day after its IST calendar day), never the clock.
A manual solve (`POST /v1/hubs/{id}/route-plans`) runs in the background right after its request.
If that solve is lost (a restart), the plan is marked failed ("abandoned") once it has been
queued or solving for 10 minutes, so the manager can ask again; it is not solved again. The job
also solves any plan still queued and younger than that. Each plan is solved in its own savepoint,
so one hub's error never undoes another hub's published plan.
"""

import asyncio
import uuid
from datetime import timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx2
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gharbasket.modules.hub_ops import api as hub_ops_api
from gharbasket.modules.logistics import execution, plans, tracking
from gharbasket.modules.logistics.models import Route, RoutePlan
from gharbasket.platform import outbox
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.jobs import JobContext, job
from gharbasket.platform.settings import Settings

IST = ZoneInfo("Asia/Kolkata")
log = structlog.get_logger("gharbasket.logistics")
RETENTION = timedelta(days=90)  # GPS trails (handbook 13 §13.5)


@job("solve_routes", "Solve tomorrow's box routes at every active hub (T-1 23:15 IST)")
async def solve_routes(ctx: JobContext) -> dict[str, Any]:
    day = ctx.scheduled_for.astimezone(IST).date() + timedelta(days=1)
    abandoned = await plans.fail_stale(ctx.session)
    queued = list(
        (
            await ctx.session.execute(
                select(RoutePlan)
                .where(RoutePlan.status == "queued")
                .order_by(RoutePlan.id)
                .with_for_update(skip_locked=True)  # not one a manual solve is running
            )
        ).scalars()
    )
    skipped = 0
    for hub_id in await hub_ops_api.active_hub_ids(ctx.session):
        if await plans.has_job_plan(ctx.session, hub_id, day, "box"):
            skipped += 1
            continue
        try:
            plan = await plans.request_solve(ctx.session, hub_id, day, "box", trigger="job")
        except ProblemError:  # a manual solve in flight, or the day's routes have started
            skipped += 1
            continue
        queued.append(plan)
    outcomes = []
    for plan in queued:
        plan_id = plan.id
        try:
            async with ctx.session.begin_nested():
                await plans.run(ctx.session, plan, http=ctx.http, settings=ctx.settings)
        except Exception as exc:  # this hub only: the others keep their plans
            log.error("route_job_failed", plan_id=str(plan_id), error=type(exc).__name__)
            await ctx.session.refresh(plan)
            plan.status, plan.error = "failed", f"unexpected error: {type(exc).__name__}"
            await ctx.session.flush()
        outcomes.append(plan.status)
    return {
        "delivery_date": day.isoformat(),
        "plans": len(queued),
        "published": outcomes.count("published"),
        "solved": outcomes.count("solved"),
        "failed": outcomes.count("failed"),
        "skipped": skipped,
        "abandoned": abandoned,
    }


# --- a manual solve, right after its request ------------------------------------------------------

_tasks: set[asyncio.Task[None]] = set()


async def solve_queued(
    sessionmaker: async_sessionmaker[AsyncSession],
    http: httpx2.AsyncClient | None,
    settings: Settings,
    plan_id: uuid.UUID,
) -> None:
    """Solve one queued plan in its own session and commit; the request has returned 202."""
    try:
        async with sessionmaker() as session:
            plan = await session.get(RoutePlan, plan_id, with_for_update=True)
            if plan is None or plan.status != "queued":
                return
            await plans.run(session, plan, http=http, settings=settings)
            await session.commit()
            if session.info.get("outbox_published"):
                outbox.relay_soon(sessionmaker)
    except Exception as exc:  # the job (or the stale rule) picks the plan up again
        log.warning("route_solve_deferred", plan_id=str(plan_id), error=type(exc).__name__)


def solve_soon(
    sessionmaker: async_sessionmaker[AsyncSession] | None,
    http: httpx2.AsyncClient | None,
    settings: Settings,
    plan_id: uuid.UUID,
) -> None:
    """Start the solve in the background (ADR-0021: no worker); without a database pool (tests)
    the plan stays queued for `solve_routes`."""
    if sessionmaker is None:
        return
    task = asyncio.create_task(solve_queued(sessionmaker, http, settings, plan_id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


# --- GPS and the day's end (§6) ---------------------------------------------------------------


@job("persist_gps_pings", "Move the riders' GPS pings from Valkey into Postgres (every minute)")
async def persist_gps_pings(ctx: JobContext) -> dict[str, Any]:
    read, inserted = await tracking.persist(ctx.session, ctx.valkey)
    return {"read": read, "inserted": inserted}


@job("close_routes", "Complete the routes still in progress; their bags ride back (T 16:00 IST)")
async def close_routes(ctx: JobContext) -> dict[str, Any]:
    day = ctx.scheduled_for.astimezone(IST).date()
    routes = (
        await ctx.session.execute(
            select(Route)
            .where(Route.status == "in_progress", Route.delivery_date <= day)
            .order_by(Route.id)
            .with_for_update(skip_locked=True)  # not one a rider is completing right now
        )
    ).scalars()
    closed = back = 0
    for route in list(routes):
        back += await execution.close(ctx.session, route, "system", None)
        route.version += 1
        await tracking.forget(ctx.valkey, route.id)
        closed += 1
    return {"date": day.isoformat(), "closed": closed, "returned_to_hub": back}


@job("purge_gps_pings", "Delete GPS pings older than 90 days (daily 03:43 IST)")
async def purge_gps_pings(ctx: JobContext) -> dict[str, Any]:
    deleted, dropped = await tracking.purge(ctx.session, ctx.scheduled_for - RETENTION)
    return {"deleted": deleted, "partitions_dropped": dropped}
