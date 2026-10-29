"""Route plans (spec §3.2): ask for a solve, run it, publish it, and read plans and their routes.

A plan is `queued` when asked for, `solving` while `solver.solve` runs, then `solved` (or
`failed`). Publishing makes its routes `published` and supersedes the date's previous plan
(cancelling its routes) and the date's other solved plans. One plan per hub, date and kind may
be in flight and one published (partial unique indexes). Publishing takes an advisory lock on
(hub, date, kind).
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import httpx2
import structlog
from sqlalchemy import ColumnElement, case, func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community import api as community_api
from gharbasket.modules.logistics import events, solver, tiffin_plans
from gharbasket.modules.logistics.models import (
    IN_FLIGHT,
    STARTED,
    Route,
    RoutePlan,
    RouteStop,
    StopOrder,
    Vehicle,
)
from gharbasket.platform import audit
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.outbox import publish as publish_event
from gharbasket.platform.settings import Settings

log = structlog.get_logger("gharbasket.logistics")
# A plan still queued or solving after this was lost (a restart mid-solve): it fails, so it does
# not block the next solve with logistics.solve_running.
STALE_AFTER = timedelta(minutes=10)


def plan_not_found() -> ProblemError:
    return ProblemError(404, "logistics.plan_not_found", "No such route plan.")


def _routes_started() -> ProblemError:
    return ProblemError(
        409, "logistics.routes_started", "A route of the published plan has already started."
    )


async def fail_stale(session: AsyncSession) -> int:
    failed = await session.execute(
        update(RoutePlan)
        .where(
            RoutePlan.status.in_(IN_FLIGHT),
            RoutePlan.created_at < func.now() - STALE_AFTER,
        )
        .values(status="failed", error="abandoned: not solved within 10 minutes")
        .returning(RoutePlan.id)
        .execution_options(synchronize_session="fetch")
    )
    return len(failed.all())


async def published_plan(
    session: AsyncSession, hub_id: uuid.UUID, day: date, kind: str
) -> RoutePlan | None:
    return await session.scalar(
        select(RoutePlan).where(
            RoutePlan.hub_id == hub_id,
            RoutePlan.delivery_date == day,
            RoutePlan.kind == kind,
            RoutePlan.status == "published",
        )
    )


async def _started(session: AsyncSession, plan_id: uuid.UUID) -> bool:
    found = await session.scalar(
        select(Route.id).where(Route.plan_id == plan_id, Route.status.in_(STARTED)).limit(1)
    )
    return found is not None


async def request_solve(
    session: AsyncSession,
    hub_id: uuid.UUID,
    day: date,
    kind: str,
    *,
    trigger: str,
    requested_by: uuid.UUID | None = None,
) -> RoutePlan:
    """A queued plan, or 409 `logistics.solve_running` / `logistics.routes_started`."""
    await fail_stale(session)
    current = await published_plan(session, hub_id, day, kind)
    if current is not None and await _started(session, current.id):
        raise _routes_started()
    plan = RoutePlan(
        hub_id=hub_id,
        delivery_date=day,
        kind=kind,
        trigger=trigger,
        requested_by=requested_by,
    )
    try:
        async with session.begin_nested():
            session.add(plan)
            await session.flush()
    except IntegrityError:
        raise ProblemError(
            409,
            "logistics.solve_running",
            "A solve for this hub, date and kind is already queued or running.",
        ) from None
    await session.refresh(plan)
    return plan


async def run(
    session: AsyncSession,
    plan: RoutePlan,
    *,
    http: httpx2.AsyncClient | None,
    settings: Settings,
) -> None:
    """Solve a queued plan in the caller's transaction. A solve that cannot start ends `failed`
    with its error; a job's plan is published at once when it has no problems, otherwise M7
    publishes `logistics.plan_needs_review`."""
    plan_id, error = plan.id, None
    plan.status = "solving"
    await session.flush()
    try:
        async with session.begin_nested():
            if plan.kind == "tiffin":  # in M7, without the ML service (§3.7)
                await tiffin_plans.solve(session, plan)
            else:
                await solver.solve(session, plan, http=http, settings=settings)
    except solver.SolveFailed as exc:
        error = str(exc)
    except Exception as exc:  # a bug must not leave the plan in flight
        log.error("route_solve_failed", plan_id=str(plan_id), error=type(exc).__name__)
        error = f"unexpected error: {type(exc).__name__}"
    if error is not None:
        await session.refresh(plan)  # the savepoint's changes are gone
        plan.status, plan.error = "failed", error
    await session.flush()
    if plan.status != "solved" or plan.trigger != "job":
        return
    problems = (plan.summary or {}).get("problems", [])
    if not problems and not plan.unserved:
        try:
            await publish(session, plan, user_id=None)
            return
        except ProblemError as exc:
            problems = [exc.detail or exc.code]
    await publish_event(
        session,
        events.PLAN_NEEDS_REVIEW,
        {
            "plan_id": str(plan.id),
            "hub_id": str(plan.hub_id),
            "delivery_date": plan.delivery_date.isoformat(),
            "kind": plan.kind,
            "problems": problems or [f"not served: {', '.join(plan.unserved)}"],
        },
    )


async def has_job_plan(session: AsyncSession, hub_id: uuid.UUID, day: date, kind: str) -> bool:
    """True once the date has a published plan, or a job's plan that did not fail: the
    nightly job then leaves the hub alone, so running it twice solves once."""
    found = await session.scalar(
        select(RoutePlan.id)
        .where(
            RoutePlan.hub_id == hub_id,
            RoutePlan.delivery_date == day,
            RoutePlan.kind == kind,
            (RoutePlan.status == "published")
            | ((RoutePlan.trigger == "job") & (RoutePlan.status != "failed")),
        )
        .limit(1)
    )
    return found is not None


async def route_published(
    session: AsyncSession,
    plan: RoutePlan,
    route: Route,
    previous_rider_user_id: uuid.UUID | None = None,
) -> None:
    """`logistics.route_published` for a published route; again with the previous rider when a
    hub manager changes the rider (§3.3)."""
    stops = list(
        (
            await session.execute(
                select(RouteStop).where(RouteStop.route_id == route.id).order_by(RouteStop.seq)
            )
        ).scalars()
    )
    captains: dict[uuid.UUID, str | None] = {}
    for society_id in {s.society_id for s in stops if s.society_id is not None}:
        captain = await community_api.captain_for_society(session, society_id)
        captains[society_id] = None if captain is None else str(captain.user_id)
    await publish_event(
        session,
        events.ROUTE_PUBLISHED,
        {
            "route_id": str(route.id),
            "plan_id": str(plan.id),
            "hub_id": str(plan.hub_id),
            "delivery_date": plan.delivery_date.isoformat(),
            "kind": plan.kind,
            "trip": route.trip,
            "vehicle_id": str(route.vehicle_id),
            "rider_user_id": None if route.rider_user_id is None else str(route.rider_user_id),
            "previous_rider_user_id": None
            if previous_rider_user_id is None
            else str(previous_rider_user_id),
            "depart": route.depart_time.strftime("%H:%M"),
            "stops": [
                {
                    "seq": s.seq,
                    "kind": s.kind,
                    "society_id": None if s.society_id is None else str(s.society_id),
                    "slot_id": None if s.slot_id is None else str(s.slot_id),
                    "eta": s.eta.strftime("%H:%M"),
                    "bags": s.bags,
                    "captain_user_id": None if s.society_id is None else captains[s.society_id],
                    # The kitchen of a tiffin route's kitchen stop (§3.7), else null
                    "partner_id": None if s.partner_id is None else str(s.partner_id),
                }
                for s in stops
            ],
        },
    )


async def _stops_on_two_routes(session: AsyncSession, plan_id: uuid.UUID) -> list[str]:
    """`<society_id>/<slot_id>` of the plan's drops that are on more than one route or twice on
    one: such a plan never publishes, whatever its problems say."""
    rows = await session.execute(
        select(RouteStop.society_id, RouteStop.slot_id)
        .join(Route, Route.id == RouteStop.route_id)
        .where(Route.plan_id == plan_id, RouteStop.kind == "drop")
        .group_by(RouteStop.society_id, RouteStop.slot_id)
        .having(func.count() > 1)
    )
    return sorted(f"{society}/{slot}" for society, slot in rows.all())


async def _tiffins_on_two_drops(session: AsyncSession, plan_id: uuid.UUID) -> list[str]:
    """A tiffin plan's orders on more than one drop (§3.7): a society may have a drop on two
    tiffin routes, so the (society, slot) rule does not apply."""
    rows = await session.execute(
        select(StopOrder.order_id)
        .join(RouteStop, RouteStop.id == StopOrder.stop_id)
        .join(Route, Route.id == RouteStop.route_id)
        .where(Route.plan_id == plan_id, RouteStop.kind == "drop")
        .group_by(StopOrder.order_id)
        .having(func.count() > 1)
    )
    return sorted(str(order_id) for order_id in rows.scalars())


def _incomplete(plan: RoutePlan, ids: list[str], why: str) -> ProblemError:
    """`plan_incomplete` naming stops (box plans) or tiffins (tiffin plans, §3.7)."""
    tiffin = plan.kind == "tiffin"
    return ProblemError(
        409,
        "logistics.plan_incomplete",
        f"{'Tiffins' if tiffin else 'Stops with orders'} {why}",
        extra={"order_ids" if tiffin else "stop_ids": ids},
    )


async def publish(
    session: AsyncSession,
    plan: RoutePlan,
    *,
    user_id: uuid.UUID | None,
    accept_problems: bool = False,
) -> RoutePlan:
    """Publish a solved plan (§3.2 rules); the date's published plan is superseded and its
    routes cancelled, unless one of them has started."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"logistics:plans:{plan.hub_id}:{plan.delivery_date}:{plan.kind}"},
    )
    await session.refresh(plan)
    if plan.status != "solved":
        raise ProblemError(
            409,
            "logistics.plan_not_publishable",
            f"Only a solved plan is published ({plan.status}).",
        )
    if plan.unserved:
        raise _incomplete(
            plan, plan.unserved, "are on no route; solve again once they can be served."
        )
    if plan.kind == "tiffin":
        twice = await _tiffins_on_two_drops(session, plan.id)
    else:
        twice = await _stops_on_two_routes(session, plan.id)
    if twice:
        raise _incomplete(plan, twice, "are on more than one route; solve again.")
    problems = (plan.summary or {}).get("problems", [])
    if problems and not accept_problems:
        raise ProblemError(
            409,
            "logistics.plan_has_problems",
            "The plan has problems; check them, then publish with accept_problems.",
            extra={"problems": problems},
        )
    current = await published_plan(session, plan.hub_id, plan.delivery_date, plan.kind)
    if current is not None:
        if await _started(session, current.id):
            raise _routes_started()
        current.status = "superseded"
        old = (await session.execute(select(Route).where(Route.plan_id == current.id))).scalars()
        for route in old:
            route.status, route.version = "cancelled", route.version + 1
            await publish_event(
                session,
                events.ROUTE_CANCELLED,
                {
                    "route_id": str(route.id),
                    "plan_id": str(current.id),
                    "hub_id": str(route.hub_id),
                    "delivery_date": route.delivery_date.isoformat(),
                    "rider_user_id": None
                    if route.rider_user_id is None
                    else str(route.rider_user_id),
                    "reason": "superseded",
                },
            )
        await session.flush()
    # Other solved plans of the day can no longer be published over this one (they are older
    # or newer alternatives the manager did not choose): superseded, their drafts cancelled.
    others = select(RoutePlan.id).where(
        RoutePlan.hub_id == plan.hub_id,
        RoutePlan.delivery_date == plan.delivery_date,
        RoutePlan.kind == plan.kind,
        RoutePlan.status == "solved",
        RoutePlan.id != plan.id,
    )
    await session.execute(update(Route).where(Route.plan_id.in_(others)).values(status="cancelled"))
    await session.execute(
        update(RoutePlan)
        .where(RoutePlan.id.in_(others))
        .values(status="superseded")
        .execution_options(synchronize_session="fetch")
    )
    if problems:
        await audit.record(
            session,
            actor_user_id=user_id,
            action="route_plans.published_with_problems",
            entity_type="route_plan",
            entity_id=str(plan.id),
            after={"problems": problems},
        )
    plan.status, plan.published_by = "published", user_id
    plan.published_at = datetime.now(UTC)
    await session.flush()
    routes = (
        await session.execute(select(Route).where(Route.plan_id == plan.id).order_by(Route.id))
    ).scalars()
    for route in routes:
        route.status, route.version = "published", route.version + 1
        await route_published(session, plan, route)
    await session.flush()
    return plan


# --- reads ----------------------------------------------------------------------------------------


async def get_plan(session: AsyncSession, plan_id: uuid.UUID) -> RoutePlan:
    plan = await session.get(RoutePlan, plan_id)
    if plan is None:
        raise plan_not_found()
    return plan


async def list_plans(
    session: AsyncSession, hub_id: uuid.UUID, day: date, kind: str | None
) -> list[RoutePlan]:
    stmt = select(RoutePlan).where(RoutePlan.hub_id == hub_id, RoutePlan.delivery_date == day)
    if kind is not None:
        stmt = stmt.where(RoutePlan.kind == kind)
    return list((await session.execute(stmt.order_by(RoutePlan.id.desc()))).scalars())


@dataclass(frozen=True)
class RouteRow:
    route: Route
    vehicle: Vehicle
    stops_total: int
    stops_done: int
    crates: int
    bags: int


async def routes_of(session: AsyncSession, plan_id: uuid.UUID) -> list[RouteRow]:
    return await route_rows(session, Route.plan_id == plan_id)


async def route_rows(session: AsyncSession, *where: ColumnElement[bool]) -> list[RouteRow]:
    """Routes with their vehicle and stop totals, by departure, vehicle label and trip."""
    done = case((RouteStop.status.in_(("handed_over", "skipped")), 1), else_=0)
    rows = await session.execute(
        select(
            Route,
            Vehicle,
            func.count(RouteStop.id),
            func.coalesce(func.sum(done), 0),
            func.coalesce(func.sum(RouteStop.crates), 0),
            func.coalesce(func.sum(RouteStop.bags), 0),
        )
        .join(Vehicle, Vehicle.id == Route.vehicle_id)
        .outerjoin(RouteStop, RouteStop.route_id == Route.id)
        .where(*where)
        .group_by(Route.id, Vehicle.id)
        .order_by(Route.depart_time, func.lower(Vehicle.label), Route.trip)
    )
    return [
        RouteRow(r, v, int(total), int(d), int(c), int(b)) for r, v, total, d, c, b in rows.all()
    ]
