"""Solving a tiffin plan (spec §3.7): M8's kitchens with lunch to collect become kitchen stops,
their tiffins drops at the societies, and both are put on two-wheeler trips in the API, without
the ML service (M10's v0 solver has no pickup and delivery).

Deterministic and pure up to `solve`: kitchens by `ready_by`, then nearest neighbour from the
hub; trips filled in that order up to the fleet's smallest `capacity_tiffins` in portions, never
splitting a kitchen; each trip's drops by nearest neighbour from its last kitchen; trips to the
vehicle free first, at most two each. ETAs come from `routing.replay`. The caller owns the
transaction; `solve` never commits.
"""

import time as clock_time
import uuid
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community import api as community_api
from gharbasket.modules.hub_ops import api as hub_ops_api
from gharbasket.modules.logistics import routing, solver
from gharbasket.modules.logistics.models import Route, RoutePlan, RouteStop, StopOrder, Vehicle
from gharbasket.modules.logistics.routing import MAX_TRIPS, Point, Trip, numbered, travel_s
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.partners import api as partners_api

MEAL = "lunch"  # dinner is not routed yet (§3.7, question 6)
MODEL = "tiffin-nn-v0"
KITCHEN_WINDOW_S = 30 * 60  # a kitchen stop's window: ready_by to ready_by + 30 min
RETURN_MARGIN_S = 60 * 60  # back within an hour of the latest window's end


@dataclass(frozen=True)
class Tiffin:
    order_id: uuid.UUID
    short_code: str
    portions: int
    society_id: uuid.UUID
    slot_id: uuid.UUID


@dataclass(frozen=True)
class Place:
    """A kitchen stop or a drop of a tiffin trip, with the tiffins collected or delivered there."""

    id: str
    kind: str  # kitchen | drop
    point: Point
    window: tuple[int, int]
    tiffins: tuple[Tiffin, ...]
    assignment_id: uuid.UUID | None = None  # kitchen stops
    partner_id: uuid.UUID | None = None
    society_id: uuid.UUID | None = None  # drops
    slot_id: uuid.UUID | None = None
    crates: int = 0  # 4 minutes a stop, no time per tiffin

    @property
    def portions(self) -> int:
        return sum(t.portions for t in self.tiffins)


def plan_trips(
    kitchens: Sequence[Place],
    societies: dict[uuid.UUID, Point],
    windows: dict[uuid.UUID, tuple[int, int]],
    capacity: int,
    vans: int,
    hub: Point,
) -> tuple[list[Trip], dict[str, Place], list[str]]:
    """The trips, every place on them by id, and the kitchens too big for one vehicle."""
    problems: list[str] = []
    groups: list[list[Place]] = []
    load = 0
    for kitchen in routing.order_nearest(kitchens, hub):
        if kitchen.portions > capacity:
            problems.append(
                f"kitchen {kitchen.assignment_id}: {kitchen.portions} portions, "
                f"a two-wheeler carries {capacity}"
            )
        if groups and load + kitchen.portions <= capacity:
            groups[-1].append(kitchen)
            load += kitchen.portions
        else:  # never split: a kitchen that does not fit starts the next trip (alone if too big)
            groups.append([kitchen])
            load = kitchen.portions
    places: dict[str, Place] = {}
    trips: list[Trip] = []
    free_at, trips_of = [0] * vans, [0] * vans
    for n, group in enumerate(groups, start=1):
        pairs: dict[tuple[uuid.UUID, uuid.UUID], list[Tiffin]] = defaultdict(list)
        for kitchen in group:
            for tiffin in kitchen.tiffins:
                pairs[tiffin.society_id, tiffin.slot_id].append(tiffin)
        drops = [
            Place(
                f"{society}/{slot}/{n}",
                "drop",
                societies[society],
                windows[slot],
                tuple(tiffins),
                society_id=society,
                slot_id=slot,
            )
            for (society, slot), tiffins in sorted(pairs.items())
        ]
        sequence = [*group, *routing.order_nearest(drops, group[-1].point)]
        places.update((p.id, p) for p in sequence)
        open_vans = [v for v in range(vans) if trips_of[v] < MAX_TRIPS] or list(range(vans))
        van = min(open_vans, key=lambda v: (free_at[v], v))
        first = group[0]
        start = max(free_at[van], first.window[0] - travel_s(hub, first.point))
        trip = routing.replay(Trip(van, start, [p.id for p in sequence]), hub, places)
        trips.append(trip)
        trips_of[van] += 1
        free_at[van] = trip.end_s  # a second trip leaves when the first is back (no reload)
    return trips, places, problems


def check(
    trips: Sequence[Trip],
    places: dict[str, Place],
    kitchens: Sequence[Place],
    fleet: Sequence[Vehicle],
    return_by_s: int,
) -> list[str]:
    """M7's check of a tiffin plan (§3.7): every tiffin on exactly one drop, on its kitchen's
    route and after it; every kitchen on exactly one route; the portions on board within the
    vehicle's `capacity_tiffins`; at most two routes a vehicle, the second leaving after the first
    is back; every route back by `return_by_s`."""
    problems: list[str] = []
    visits: Counter[str] = Counter()
    drops_of: dict[uuid.UUID, list[tuple[Trip, int]]] = defaultdict(list)
    back_at: dict[int, int] = {}
    for van, trip_no, trip in numbered(trips):
        label, load, peak = fleet[van].label, 0, 0
        for position, place_id in enumerate(trip.stops):
            place = places[place_id]
            visits[place_id] += 1
            if place.kind == "kitchen":
                load += place.portions
            else:
                load -= place.portions
                for tiffin in place.tiffins:
                    drops_of[tiffin.order_id].append((trip, position))
            peak = max(peak, load)
        if peak > fleet[van].capacity_tiffins:
            problems.append(f"{label} trip {trip_no}: {peak} portions on board")
        if van in back_at and trip.start_s < back_at[van]:
            problems.append(f"{label} trip {trip_no}: leaves before trip {trip_no - 1} is back")
        back_at[van] = trip.end_s
        if trip.end_s > return_by_s:
            problems.append(f"{label} trip {trip_no}: back after {routing.clock(return_by_s)}")
    for van, count in sorted(Counter(t.van for t in trips).items()):
        if count > MAX_TRIPS:
            problems.append(f"{fleet[van].label}: {count} trips, more than {MAX_TRIPS}")
    for kitchen in kitchens:
        if visits[kitchen.id] != 1:
            problems.append(f"kitchen {kitchen.assignment_id}: on {visits[kitchen.id]} routes")
        for tiffin in kitchen.tiffins:
            drops = drops_of.get(tiffin.order_id, [])
            if len(drops) != 1:
                problems.append(f"tiffin {tiffin.short_code}: on {len(drops)} drops")
            elif kitchen.id not in drops[0][0].stops[: drops[0][1]]:
                problems.append(f"tiffin {tiffin.short_code}: not after its kitchen")
    return problems


async def _kitchens(
    session: AsyncSession, refs: list[partners_api.PickupRef]
) -> tuple[
    list[Place], dict[uuid.UUID, Point], dict[uuid.UUID, tuple[int, int]], list[str], list[str]
]:
    """Kitchen stops with their located tiffins, the society points and slot windows, and the
    problems and unserved orders (a kitchen or a society without a location)."""
    wanted = [o.order_id for ref in refs for o in ref.orders]
    orders = {s.order.id: s.order for s in await orders_api.summaries(session, wanted)}
    societies = await community_api.society_points(
        session, sorted({o.society_id for o in orders.values()})
    )
    windows: dict[uuid.UUID, tuple[int, int]] = {}
    for slot_id in sorted({o.slot_id for o in orders.values()}):
        slot = await orders_api.slot_ref(session, slot_id)
        if slot is not None:
            windows[slot_id] = (solver.seconds(slot.start), solver.seconds(slot.end))
    kitchens: list[Place] = []
    problems: list[str] = []
    unserved: list[str] = []
    for ref in refs:
        if ref.lat is None or ref.lng is None or ref.ready_by is None:
            problems.append(f"kitchen {ref.assignment_id}: no location")
            unserved += [str(o.order_id) for o in ref.orders]
            continue
        tiffins = []
        for held in ref.orders:
            order = orders[held.order_id]
            if order.society_id not in societies or order.slot_id not in windows:
                problems.append(f"tiffin {held.short_code}: the society has no location")
                unserved.append(str(held.order_id))
                continue
            tiffins.append(
                Tiffin(
                    held.order_id, held.short_code, held.portions, order.society_id, order.slot_id
                )
            )
        if tiffins:
            ready = solver.seconds(ref.ready_by)
            kitchens.append(
                Place(
                    f"kitchen/{ref.assignment_id}",
                    "kitchen",
                    (ref.lat, ref.lng),
                    (ready, ready + KITCHEN_WINDOW_S),
                    tuple(tiffins),
                    assignment_id=ref.assignment_id,
                    partner_id=ref.partner_id,
                )
            )
    return kitchens, societies, windows, problems, unserved


async def solve(session: AsyncSession, plan: RoutePlan) -> None:
    """Solve `plan` (kind tiffin, status `solving`) and leave it `solved` with draft routes, or
    raise SolveFailed."""
    started = clock_time.perf_counter()
    hub = await hub_ops_api.get_hub(session, plan.hub_id)
    if hub is None:
        raise solver.SolveFailed("unknown hub")
    refs = await partners_api.tiffin_pickups(session, plan.hub_id, plan.delivery_date, MEAL)
    kitchens, societies, windows, problems, unserved = await _kitchens(session, refs)
    vehicles = await solver.fleet_of(session, plan.hub_id, plan.kind)
    trips: list[Trip] = []
    places: dict[str, Place] = {}
    figures = routing.summary([], {})
    back_s = 0
    if kitchens:
        if not vehicles:
            raise solver.SolveFailed("no active two_wheeler vehicle at the hub")
        hub_point = (hub.lat, hub.lng)
        capacity = min(v.capacity_tiffins for v in vehicles)
        latest_end = max(windows[t.slot_id][1] for k in kitchens for t in k.tiffins)
        back_s = latest_end + RETURN_MARGIN_S
        trips, places, extra = plan_trips(
            kitchens, societies, windows, capacity, len(vehicles), hub_point
        )
        problems += extra + check(trips, places, kitchens, vehicles, back_s)
        figures = routing.summary(trips, places)
    await _write_routes(session, plan, vehicles, trips, places, back_s)
    plan.source, plan.model, plan.distance_source = "nearest", MODEL, "haversine"
    plan.unserved = sorted(unserved)
    plan.summary = {
        "stops": len(places),
        "orders": sum(len(ref.orders) for ref in refs),
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
    places: dict[str, Place],
    back_s: int,
) -> None:
    captains: dict[uuid.UUID, bool] = {}
    for society_id in sorted({p.society_id for p in places.values() if p.society_id is not None}):
        captains[society_id] = (
            await community_api.captain_for_society(session, society_id) is not None
        )
    for van, trip_no, trip in numbered(trips):
        vehicle = vehicles[van]
        route = Route(
            plan_id=plan.id,
            hub_id=plan.hub_id,
            delivery_date=plan.delivery_date,
            kind=plan.kind,
            trip=trip_no,
            vehicle_id=vehicle.id,
            rider_user_id=vehicle.default_rider_user_id,
            depart_time=solver.at_clock(trip.start_s),
            return_by=solver.at_clock(back_s),
            planned_km=round(trip.metres / 1000, 2),
        )
        session.add(route)
        await session.flush()
        for seq, (place_id, eta) in enumerate(zip(trip.stops, trip.etas, strict=True), start=1):
            place = places[place_id]
            stop = RouteStop(
                route_id=route.id,
                seq=seq,
                kind=place.kind,
                society_id=place.society_id,
                slot_id=place.slot_id,
                assignment_id=place.assignment_id,
                partner_id=place.partner_id,
                portions=place.portions,
                window_start=solver.at_clock(place.window[0]),
                window_end=solver.at_clock(place.window[1]),
                eta=solver.at_clock(eta),
                late_s=max(0, eta - place.window[1]),
                # No active captain: the rider delivers at the doors (§3.4, §3.7).
                rider_mode=place.society_id is not None and not captains[place.society_id],
                orders=len(place.tiffins),
                crates=0,
                bags=len(place.tiffins),  # one per tiffin
                kg_tenths=0,  # weight is not checked: 30 portions are about 15 kg
            )
            session.add(stop)
            await session.flush()
            session.add_all(
                StopOrder(
                    stop_id=stop.id,
                    order_id=t.order_id,
                    short_code=t.short_code,
                    portions=t.portions,
                )
                for t in place.tiffins
            )
    await session.flush()
