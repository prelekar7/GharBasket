"""Rider execution (spec §3.3): start, arrive, skip and complete.

Every order move goes through `orders.api.transition` with the rider actor (the route's rider,
also when a hub manager taps for them; `hub` only for a route without a rider). Every change to a
route or one of its stops bumps the route's version (its ETag). Field taps are written to
`logistics.field_events` with the phone's `client_at`; the server's time decides everything else.
"""

import math
import secrets
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community import api as community_api
from gharbasket.modules.hub_ops import api as hub_ops_api
from gharbasket.modules.logistics import attempts, events
from gharbasket.modules.logistics.models import FieldEvent, Route, RouteStop
from gharbasket.modules.logistics.routes import Caller, conflict, get_route
from gharbasket.modules.logistics.views import IST, ist_seconds, now, stops_of
from gharbasket.modules.orders import api as orders_api
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.outbox import publish
from gharbasket.platform.permissions import Permit

ROUTE_KINDS = {"box": ("box", "addon")}  # order kinds a route of each kind carries
BAGS_PER_CRATE = 3  # M6


def stop_not_found() -> ProblemError:
    return ProblemError(404, "logistics.stop_not_found", "No such stop.")


async def get_stop(
    session: AsyncSession, stop_id: uuid.UUID, permit: Permit
) -> tuple[RouteStop, Route, Caller]:
    stop = await session.get(RouteStop, stop_id)
    if stop is None:
        raise stop_not_found()
    try:
        route, who = await get_route(session, stop.route_id, permit, for_update=True, acting=True)
    except ProblemError:
        raise stop_not_found() from None
    await session.refresh(stop)  # what it is now that the route is ours
    return stop, route, who


def ensure_driver(route: Route, who: Caller) -> None:
    """The route's rider, or a hub manager of its hub (403 `logistics.not_your_route`)."""
    if not who.manager and route.rider_user_id != who.user_id:
        raise ProblemError(403, "logistics.not_your_route", "This route is another rider's.")


def ensure_running(route: Route) -> None:
    if route.status in ("draft", "published"):
        raise conflict("logistics.route_not_started", "Start the route first.")
    if route.status in ("completed", "cancelled"):
        raise conflict("logistics.route_closed", f"This route is {route.status}.")


def order_actor(route: Route, who: Caller) -> tuple[str, uuid.UUID]:
    """The actor of the orders' moves: the route's rider (paid per dispatched order, M8 §3.6),
    also when a hub manager taps for them; `hub` for a route without a rider."""
    if route.rider_user_id is not None:
        return "rider", route.rider_user_id
    return "hub", who.user_id


def tap(
    session: AsyncSession,
    route: Route,
    action: str,
    who: Caller,
    client_at: datetime | None,
    *,
    stop: RouteStop | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    route.version += 1
    session.add(
        FieldEvent(
            route_id=route.id,
            stop_id=None if stop is None else stop.id,
            action=action,
            user_id=who.user_id,
            payload=payload,
            client_at=client_at,
        )
    )


async def route_orders(
    session: AsyncSession, route: Route, statuses: tuple[str, ...]
) -> dict[uuid.UUID, list[orders_api.Order]]:
    """The orders of each stop of the route (its society and slot on the route's date)."""
    stops = await stops_of(session, route.id)
    by_pair = {(s.society_id, s.slot_id): s.id for s in stops}
    out: dict[uuid.UUID, list[orders_api.Order]] = {s.id: [] for s in stops}
    found = await orders_api.orders_for_delivery(
        session, route.hub_id, route.delivery_date, statuses, kinds=ROUTE_KINDS[route.kind]
    )
    for summary in found:
        stop_id = by_pair.get((summary.order.society_id, summary.order.slot_id))
        if stop_id is not None:
            out[stop_id].append(summary.order)
    return out


# --- start, arrive, skip, complete --------------------------------------------------------------


async def _ensure_free(session: AsyncSession, route: Route) -> None:
    """No other route of the rider in progress, and the vehicle's earlier trips completed. The
    rider is locked first (M1 owns the user row, so a key), so two starts run one at a time."""
    if route.rider_user_id is not None:
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"logistics:rider:{route.rider_user_id}"},
        )
        busy = await session.scalar(
            select(Route.id).where(
                Route.rider_user_id == route.rider_user_id,
                Route.status == "in_progress",
                Route.id != route.id,
            )
        )
        if busy is not None:
            raise conflict(
                "logistics.rider_busy", "The rider has another route in progress.", route_id=busy
            )
    earlier = await session.scalar(
        select(Route.id).where(
            Route.plan_id == route.plan_id,
            Route.vehicle_id == route.vehicle_id,
            Route.trip < route.trip,
            Route.status.in_(("published", "in_progress")),
        )
    )
    if earlier is not None:
        raise conflict(
            "logistics.rider_busy",
            "The vehicle's earlier trip is not completed yet.",
            route_id=earlier,
        )


async def _loads(session: AsyncSession, route: Route, stops: list[RouteStop]) -> None:
    """The packed crates and bags per stop from M6 (§3.3). M6 counts per society, so a society
    with more than one stop that day keeps one bag per dispatched order instead."""
    loads = {
        x.society_id: x
        for x in await hub_ops_api.society_loads(session, route.hub_id, route.delivery_date)
    }
    stops_per_society = dict(
        (
            await session.execute(
                select(RouteStop.society_id, func.count())
                .join(Route, Route.id == RouteStop.route_id)
                .where(Route.plan_id == route.plan_id, RouteStop.kind == "drop")
                .group_by(RouteStop.society_id)
            )
        ).all()
    )
    for stop in stops:
        load = loads.get(stop.society_id) if stop.society_id is not None else None
        if load is not None and stops_per_society.get(stop.society_id) == 1:
            stop.bags, stop.crates = load.packed_bags, load.crates


async def start(
    session: AsyncSession,
    route: Route,
    who: Caller,
    *,
    leave_behind: bool,
    client_at: datetime | None,
) -> None:
    ensure_driver(route, who)
    if route.status == "draft":
        raise conflict("logistics.route_not_published", "Publish the route's plan first.")
    if route.status == "in_progress":
        raise conflict("logistics.routes_started", "This route has already started.")
    if route.status in ("completed", "cancelled"):
        raise conflict("logistics.route_closed", f"This route is {route.status}.")
    if route.delivery_date != now().astimezone(IST).date():
        raise conflict(
            "logistics.route_not_today",
            "A route starts on its own date.",
            delivery_date=route.delivery_date.isoformat(),
        )
    await _ensure_free(session, route)
    per_stop = await route_orders(session, route, ("locked", "picking", "packed"))
    unpacked = sorted(o.id for orders in per_stop.values() for o in orders if o.status != "packed")
    if unpacked and not leave_behind:
        raise conflict(
            "logistics.bags_not_packed",
            "Some bags of this route are not packed yet; wait, or start with leave_behind.",
            order_ids=[str(o) for o in unpacked],
        )
    actor, actor_user = order_actor(route, who)
    stops = await stops_of(session, route.id)
    for stop in stops:
        packed = [o for o in per_stop[stop.id] if o.status == "packed"]
        for order in packed:
            await orders_api.transition(
                session, order.id, "dispatched", actor=actor, actor_user_id=actor_user
            )
        await attempts.open_attempts(session, stop, packed)
        stop.bags, stop.crates = len(packed), math.ceil(len(packed) / BAGS_PER_CRATE)
        stop.handover_code = f"{secrets.randbelow(10_000):04d}"
        stop.code_attempts = 0
        if stop.society_id is not None and not stop.rider_mode:
            stop.rider_mode = (
                await community_api.captain_for_society(session, stop.society_id) is None
            )
    await _loads(session, route, stops)
    route.status, route.started_at, route.left_behind = "in_progress", now(), unpacked
    tap(session, route, "start", who, client_at, payload={"left_behind": len(unpacked)})
    await session.flush()


async def arrive(
    session: AsyncSession, route: Route, stop: RouteStop, who: Caller, client_at: datetime | None
) -> None:
    """Records the arrival (once; a later tap changes nothing) and the stop's delay, which moves
    the remaining stops' `eta_now`, and tells the captain (`logistics.stop_arrived`)."""
    ensure_driver(route, who)
    ensure_running(route)
    if stop.status != "pending":
        return
    at = now()
    stop.status, stop.arrived_at = "arrived", at
    stop.delay_s = ist_seconds(at) - (stop.eta.hour * 3600 + stop.eta.minute * 60)
    captain = (
        None
        if stop.society_id is None
        else await community_api.captain_for_society(session, stop.society_id)
    )
    tap(session, route, "arrive", who, client_at, stop=stop)
    await publish(
        session,
        events.STOP_ARRIVED,
        {
            "stop_id": str(stop.id),
            "route_id": str(route.id),
            "hub_id": str(route.hub_id),
            "society_id": None if stop.society_id is None else str(stop.society_id),
            "captain_user_id": None if captain is None else str(captain.user_id),
            "at": at.isoformat(),
        },
    )
    await session.flush()


async def skip(
    session: AsyncSession,
    route: Route,
    stop: RouteStop,
    who: Caller,
    *,
    reason: str,
    note: str | None,
    client_at: datetime | None,
) -> None:
    """The stop's orders stay dispatched and ride back to the hub (`returned_to_hub` at
    complete). Skipping a skipped stop changes nothing."""
    ensure_driver(route, who)
    ensure_running(route)
    if stop.status == "handed_over":
        raise conflict("logistics.already_handed_over", "This stop was handed over.")
    if stop.status == "skipped":
        return
    stop.status, stop.skip_reason = "skipped", reason
    tap(session, route, "skip", who, client_at, stop=stop, payload={"reason": reason, "note": note})
    await session.flush()


async def complete(
    session: AsyncSession,
    route: Route,
    who: Caller,
    *,
    empties_returned: int | None,
    client_at: datetime | None,
) -> None:
    """Every stop handed over or skipped; orders still dispatched ride back (`undelivered`,
    reason `returned_to_hub`, then §3.4's redelivery)."""
    ensure_driver(route, who)
    ensure_running(route)
    stops = await stops_of(session, route.id)
    open_stops = [s for s in stops if s.status not in ("handed_over", "skipped")]
    if open_stops:
        raise conflict(
            "logistics.stops_open",
            "Hand over or skip every stop first.",
            stop_ids=[str(s.id) for s in open_stops],
        )
    await close(session, route, *order_actor(route, who))
    route.empties_returned = empties_returned
    tap(session, route, "complete", who, client_at, payload={"empties_returned": empties_returned})
    await session.flush()


async def close(
    session: AsyncSession, route: Route, actor: str, actor_user: uuid.UUID | None
) -> int:
    """Complete a started route: its dispatched orders come back to the hub; publishes
    `logistics.route_completed`. Returns the number of orders that came back."""
    per_stop = await route_orders(session, route, ("dispatched",))
    back = [o for orders in per_stop.values() for o in orders]
    for order in back:
        await orders_api.transition(
            session,
            order.id,
            "undelivered",
            actor=actor,
            actor_user_id=actor_user,
            payload={"reason": "returned_to_hub"},
        )
    await attempts.set_status(
        session,
        [o.id for o in back],
        "undelivered",
        reason="returned_to_hub",
        by_actor=actor,
        user_id=actor_user,
    )
    stops = await stops_of(session, route.id)
    done_at = now()
    route.status, route.completed_at = "completed", done_at
    await publish(
        session,
        events.ROUTE_COMPLETED,
        {
            "route_id": str(route.id),
            "hub_id": str(route.hub_id),
            "delivery_date": route.delivery_date.isoformat(),
            "rider_user_id": None if route.rider_user_id is None else str(route.rider_user_id),
            "vehicle_id": str(route.vehicle_id),
            "stops_done": sum(s.status == "handed_over" for s in stops),
            "stops_skipped": sum(s.status == "skipped" for s in stops),
            "km": route.planned_km,
            "started_at": None if route.started_at is None else route.started_at.isoformat(),
            "completed_at": done_at.isoformat(),
        },
    )
    return len(back)
