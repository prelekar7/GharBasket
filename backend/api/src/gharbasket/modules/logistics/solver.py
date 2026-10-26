"""Solving one route plan (spec §3.2): the day's orders become stops, the stops and the fleet go
to M10's solver through `intel_gateway.api.solve_routes`, and when that answers None (the ML
service is down, slow or not configured) M7 builds the plan itself. Either way M7's structural
check runs and the routes and stops are written as drafts.

The caller owns the transaction; `solve` never commits.
"""

import math
import time as clock_time
import uuid
from collections import defaultdict
from datetime import date, time, timedelta

import httpx2
import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.community import api as community_api
from gharbasket.modules.hub_ops import api as hub_ops_api
from gharbasket.modules.intel_gateway import api as intel_api
from gharbasket.modules.logistics import attempts, pickups, routing
from gharbasket.modules.logistics.models import Route, RoutePlan, RouteStop, Vehicle
from gharbasket.modules.logistics.routing import Stop, Trip, Van
from gharbasket.modules.orders import api as orders_api
from gharbasket.platform.settings import Settings

VEHICLE_KIND = {"box": "cargo_3w", "tiffin": "two_wheeler"}
ORDER_KINDS = ("box", "addon")
# Picking (M6, from 05:00) too: a re-solve during packing keeps those societies on the plan.
ORDER_STATUSES = ("locked", "picking", "packed")
BAGS_PER_CRATE = 3  # M6's three bags to a crate
STEP_GRAMS = 500  # a step of a product without a weight
RETURN_MARGIN_S = 90 * 60  # back within 90 minutes of the latest window's end
TIME_LIMIT_S = 20.0  # two waves of 10 s
PREVIOUS_DAYS = 7
log = structlog.get_logger("gharbasket.logistics")


class SolveFailed(Exception):
    """A solve that ends before any plan exists: the plan becomes `failed` with this error."""


def seconds(at: time) -> int:
    return at.hour * 3600 + at.minute * 60


def at_clock(value: int) -> time:
    return time.fromisoformat(routing.clock(value))


async def _stops(
    session: AsyncSession, hub_id: uuid.UUID, day: date
) -> tuple[dict[str, Stop], dict[str, uuid.UUID], int]:
    """The located stops by id, the stops without a location (id → society) and the orders."""
    found = await orders_api.orders_for_delivery(
        session, hub_id, day, statuses=ORDER_STATUSES, kinds=ORDER_KINDS
    )
    # Plus the redeliveries M7 scheduled for the day (§3.4); a bag the captain kept overnight
    # needs the stop but rides on no van.
    again = await attempts.redeliveries_for(session, hub_id, day)
    kept = {d.order_id for d in again if d.kept_by_captain}
    found = [*found, *await orders_api.summaries(session, [d.order_id for d in again])]
    lines = [(x.order.id, line) for x in found if x.order.id not in kept for line in x.lines]
    weights = await catalog_api.line_grams(
        session,
        [(ln.product_id, ln.qty - ln.shortage_qty, ln.step_qty, ln.step_unit) for _, ln in lines],
    )
    grams: dict[uuid.UUID, float] = defaultdict(float)
    for (order_id, line), weight in zip(lines, weights, strict=True):
        steps = line.qty - line.shortage_qty
        grams[order_id] += STEP_GRAMS * steps if weight is None else weight
    groups: dict[tuple[uuid.UUID, uuid.UUID], list[uuid.UUID]] = defaultdict(list)
    for x in found:
        groups[x.order.society_id, x.order.slot_id].append(x.order.id)
    points = await community_api.society_points(session, sorted({s for s, _ in groups}))
    slots = {slot_id: await orders_api.slot_ref(session, slot_id) for _, slot_id in groups}
    located: dict[str, Stop] = {}
    unlocated: dict[str, uuid.UUID] = {}
    # A society's open empties ride on its earliest-window stop (§3.6).
    empties = await pickups.open_empties(session, sorted({s for s, _ in groups}))
    first: dict[uuid.UUID, uuid.UUID] = {}
    for society_id, slot_id in sorted(groups, key=lambda g: (_start(slots[g[1]]), g)):
        if society_id in points and slots[slot_id] is not None:
            first.setdefault(society_id, slot_id)
    for (society_id, slot_id), order_ids in sorted(groups.items()):
        stop_id, slot = f"{society_id}/{slot_id}", slots[slot_id]
        if society_id not in points or slot is None:
            unlocated[stop_id] = society_id
            continue
        located[stop_id] = Stop(
            id=stop_id,
            society_id=society_id,
            slot_id=slot_id,
            point=points[society_id],
            window=(seconds(slot.start), seconds(slot.end)),
            orders=len(order_ids),
            crates=math.ceil(len([o for o in order_ids if o not in kept]) / BAGS_PER_CRATE),
            kg_tenths=math.ceil(sum(grams[o] for o in order_ids) / 100),
            empties=empties.get(society_id, 0) if first.get(society_id) == slot_id else 0,
        )
    return located, unlocated, len(found)


def _start(slot: orders_api.SlotRef | None) -> int:
    return 0 if slot is None else seconds(slot.start)


async def _fleet(session: AsyncSession, hub_id: uuid.UUID, kind: str) -> list[Vehicle]:
    rows = await session.execute(
        select(Vehicle)
        .where(Vehicle.hub_id == hub_id, Vehicle.kind == VEHICLE_KIND[kind], Vehicle.active)
        .order_by(func.lower(Vehicle.label), Vehicle.id)
    )
    return list(rows.scalars())


async def _previous_order(session: AsyncSession, plan: RoutePlan) -> list[uuid.UUID]:
    """Societies in the stop order of the hub's last published plan of this kind in the past
    7 days; empty when there is none."""
    previous = await session.scalar(
        select(RoutePlan.id)
        .where(
            RoutePlan.hub_id == plan.hub_id,
            RoutePlan.kind == plan.kind,
            RoutePlan.status == "published",
            RoutePlan.delivery_date < plan.delivery_date,
            RoutePlan.delivery_date >= plan.delivery_date - timedelta(days=PREVIOUS_DAYS),
        )
        .order_by(RoutePlan.delivery_date.desc())
        .limit(1)
    )
    if previous is None:
        return []
    rows = await session.execute(
        select(RouteStop.society_id)
        .join(Route, Route.id == RouteStop.route_id)
        .where(Route.plan_id == previous, RouteStop.society_id.is_not(None))
        .order_by(Route.depart_time, Route.trip, Route.id, RouteStop.seq)
    )
    return list(dict.fromkeys(s for s in rows.scalars() if s is not None))


def _request(
    hub: hub_ops_api.HubRef, stops: dict[str, Stop], fleet: list[Van], depart_s: int, back_s: int
) -> intel_api.RouteRequest:
    return intel_api.RouteRequest.model_validate(
        {
            "hub": {"lat": hub.lat, "lng": hub.lng},
            "depart_s": depart_s,
            "return_by_s": back_s,
            "stops": [
                {
                    "id": s.id,
                    "lat": s.point[0],
                    "lng": s.point[1],
                    "orders": s.orders,
                    "kg_tenths": s.kg_tenths,
                    "crates": s.crates,
                    "empties": s.empties,
                    "window_start_s": s.window[0],
                    "window_end_s": s.window[1],
                }
                for s in stops.values()
            ],
            # The v0 solver takes one capacity per fleet: a mixed fleet is solved with the
            # smallest of each measure (§3.1).
            "vans": {
                "count": len(fleet),
                "capacity_kg": min(v.capacity_kg for v in fleet),
                "capacity_crates": min(v.capacity_crates for v in fleet),
            },
            "time_limit_s": TIME_LIMIT_S,
        }
    )


def _from_ml(
    answer: intel_api.RoutePlan, fleet: list[Van], stops: dict[str, Stop], hub: routing.Point
) -> tuple[list[Trip], list[str]]:
    """The ML service's trips with van index i as the i-th vehicle by label. A van outside the
    fleet and a stop that is not one of this plan's drops (no food bank is sent in S5) are
    named in the problems and left off."""
    trips, problems = [], []
    for route in answer.routes:
        if route.van >= len(fleet):
            problems.append(f"van {route.van}: not in the fleet")
            continue
        unknown = [s.id for s in route.stops if s.kind != "drop" or s.id not in stops]
        if unknown:
            problems.append(f"van {route.van}: unknown stops " + ", ".join(unknown))
        drops = [s for s in route.stops if s.kind == "drop" and s.id in stops]
        trip = Trip(route.van, route.start_s, [s.id for s in drops], [s.eta_s for s in drops])
        trip.end_s = route.end_s
        trip.metres = routing.replay(Trip(route.van, route.start_s, trip.stops), hub, stops).metres
        if trip.stops:
            trips.append(trip)
    return trips, problems


async def solve(
    session: AsyncSession,
    plan: RoutePlan,
    *,
    http: httpx2.AsyncClient | None,
    settings: Settings,
) -> None:
    """Solve `plan` (status `solving`) and leave it `solved` with draft routes, or raise
    SolveFailed. ML first; with no answer `fallback_previous`, else `fallback_nearest`."""
    started = clock_time.perf_counter()
    hub = await hub_ops_api.get_hub(session, plan.hub_id)
    if hub is None:
        raise SolveFailed("unknown hub")
    stops, unlocated, order_count = await _stops(session, plan.hub_id, plan.delivery_date)
    vehicles = await _fleet(session, plan.hub_id, plan.kind)
    fleet = [Van(v.label, v.capacity_kg, v.capacity_crates) for v in vehicles]
    hub_point = (hub.lat, hub.lng)
    trips: list[Trip] = []
    problems = [f"{stop_id}: the society has no location" for stop_id in unlocated]
    figures: dict[str, float | int] = routing.summary([], {})
    depart_s = seconds(time.fromisoformat(hub.timings.get("dispatch", "06:30")))
    back_s = depart_s
    if order_count:
        if not vehicles:
            raise SolveFailed(f"no active {VEHICLE_KIND[plan.kind]} vehicle at the hub")
        if not stops:
            raise SolveFailed("no orders with a location")
        latest_end = max(s.window[1] for s in stops.values())
        back_s = max(latest_end + RETURN_MARGIN_S, depart_s + 3600)
        answer = None
        try:
            request = _request(hub, stops, fleet, depart_s, back_s)
        except ValueError as exc:  # more stops or vans than M10 takes: M7 plans it alone
            log.warning("route_request_invalid", plan_id=str(plan.id), error=type(exc).__name__)
            request = None
        if http is not None and request is not None:
            answer = await intel_api.solve_routes(
                http, settings, request, session=session, hub_id=hub.id, for_date=plan.delivery_date
            )
        if answer is not None:
            trips, extra = _from_ml(answer, fleet, stops, hub_point)
            problems += extra + answer.summary.problems
            figures = answer.summary.model_dump(include={"km", "cost_paise", "on_time", "late_min"})
            # M10 answers `fallback` when its solver found nothing and it used nearest neighbour.
            plan.source = "ml" if answer.status == "solved" else "ml_fallback"
            plan.distance_source, plan.model = answer.source, answer.model
            prediction = await intel_api.latest_prediction(
                session, kind="route", subject_id=hub.id, for_date=plan.delivery_date
            )
            plan.prediction_id = None if prediction is None else prediction.id
        else:
            previous = await _previous_order(session, plan)
            if previous:
                sequence = routing.order_previous(list(stops.values()), previous)
                plan.source = "fallback_previous"
            else:
                sequence = routing.order_nearest(list(stops.values()), hub_point)
                plan.source = "fallback_nearest"
            trips = routing.fill(sequence, fleet, hub_point, depart_s)
            figures = routing.summary(trips, stops)
            plan.distance_source = "haversine"
        problems += routing.check(trips, stops, fleet, depart_s, back_s)
    routed = {s for t in trips for s in t.stops}
    plan.unserved = sorted(set(unlocated) | (set(stops) - routed))
    await _write_routes(session, plan, vehicles, trips, stops, back_s)
    plan.summary = {
        "stops": len(stops) + len(unlocated),
        "orders": order_count,
        "vehicles": len({t.van for t in trips}),
        "routes": len(trips),
        **figures,
        "problems": list(dict.fromkeys(problems)),
    }
    plan.status = "solved"
    plan.seconds = round(clock_time.perf_counter() - started, 3)


async def _write_routes(
    session: AsyncSession,
    plan: RoutePlan,
    vehicles: list[Vehicle],
    trips: list[Trip],
    stops: dict[str, Stop],
    back_s: int,
) -> None:
    captains: dict[uuid.UUID, bool] = {}
    for society_id in sorted({stops[s].society_id for t in trips for s in t.stops}):
        captains[society_id] = (
            await community_api.captain_for_society(session, society_id) is not None
        )
    for van, trip_no, trip in routing.numbered(trips):
        vehicle = vehicles[van]
        route = Route(
            plan_id=plan.id,
            hub_id=plan.hub_id,
            delivery_date=plan.delivery_date,
            kind=plan.kind,
            trip=trip_no,
            vehicle_id=vehicle.id,
            rider_user_id=vehicle.default_rider_user_id,
            depart_time=at_clock(trip.start_s),
            return_by=at_clock(back_s),
            planned_km=round(trip.metres / 1000, 2),
        )
        session.add(route)
        await session.flush()
        for seq, (stop_id, eta) in enumerate(zip(trip.stops, trip.etas, strict=True), start=1):
            stop = stops[stop_id]
            session.add(
                RouteStop(
                    route_id=route.id,
                    seq=seq,
                    kind="drop",
                    society_id=stop.society_id,
                    slot_id=stop.slot_id,
                    window_start=at_clock(stop.window[0]),
                    window_end=at_clock(stop.window[1]),
                    eta=at_clock(eta),
                    late_s=max(0, eta - stop.window[1]),
                    # No active captain: the rider hands over and does the doors (§3.4).
                    rider_mode=not captains[stop.society_id],
                    orders=stop.orders,
                    crates=stop.crates,
                    bags=stop.orders,  # one bag per order (M6)
                    kg_tenths=stop.kg_tenths,
                    empties_expected=stop.empties,
                )
            )
    await session.flush()
