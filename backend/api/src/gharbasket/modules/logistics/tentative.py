"""A day's tentative box routes for green-slot pricing (M10 §3.8, the facade's `tentative_day`).

A day with a published box plan has its routes as published. Otherwise the routes are what M7's
own fallback would publish if it ran now (§3.2: `fallback_previous`, else `fallback_nearest`), over
the boxes M3 expects or over M4's orders. Read-only, and no new routing code: `routing` decides.
"""

import math
import uuid
from datetime import date, time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community import api as community_api
from gharbasket.modules.hub_ops import api as hub_ops_api
from gharbasket.modules.intel_gateway import api as intel_api
from gharbasket.modules.logistics import routing, solver
from gharbasket.modules.logistics.models import Route, RoutePlan, RouteStop, Vehicle
from gharbasket.modules.logistics.routing import Stop, Van
from gharbasket.modules.orders import api as orders_api

TENTATIVE_BOX_KG_TENTHS = 80  # 8 kg a box M3 expects
KIND = "box"


async def tentative_day(
    session: AsyncSession,
    hub_id: uuid.UUID,
    day: date,
    *,
    expected: dict[tuple[uuid.UUID, uuid.UUID], int] | None = None,
) -> intel_api.SlotDay | None:
    """The hub's stops, trips, fleet, times and active box slots on `day`; None without the hub
    or an active box vehicle. A published plan wins over `expected`."""
    hub = await hub_ops_api.get_hub(session, hub_id)
    vehicles = await solver.fleet_of(session, hub_id, KIND)
    if hub is None or not vehicles:
        return None
    fleet = [Van(v.label, v.capacity_kg, v.capacity_crates) for v in vehicles]
    hub_point = (hub.lat, hub.lng)
    depart_s = solver.seconds(time.fromisoformat(hub.timings.get("dispatch", "06:30")))
    slots = await orders_api.box_slots(session, hub_id, day)
    published = await session.scalar(
        select(RoutePlan.id).where(
            RoutePlan.hub_id == hub_id,
            RoutePlan.delivery_date == day,
            RoutePlan.kind == KIND,
            RoutePlan.status == "published",
        )
    )
    if published is not None:
        stops, trips = await _published(session, published, vehicles)
        source = "published"
    else:
        if expected is None:
            stops = (await solver.order_stops(session, hub_id, day))[0]
        else:
            stops = await _expected(session, expected, slots)
        previous = await solver.previous_order(session, hub_id, KIND, day)
        if previous:
            sequence, source = routing.order_previous(list(stops.values()), previous), "previous"
        else:
            sequence, source = routing.order_nearest(list(stops.values()), hub_point), "nearest"
        filled = routing.fill(sequence, fleet, hub_point, depart_s)
        trips = [(t.van, t.stops) for _, _, t in routing.numbered(filled)]
    windows = [s.window for s in stops.values()]
    windows += [(solver.seconds(s.start), solver.seconds(s.end)) for s in slots]
    latest_end = max((end for _, end in windows), default=depart_s)
    return intel_api.SlotDay.model_validate(
        {
            "hub_id": hub_id,
            "day": day,
            "routes_source": source,
            "hub": {"lat": hub.lat, "lng": hub.lng},
            "depart_s": depart_s,
            "return_by_s": max(latest_end + solver.RETURN_MARGIN_S, depart_s + 3600),
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
            "trips": [{"van": van, "stops": ids} for van, ids in trips],
            # One capacity per fleet, the smallest of each measure, as a solve sends it (§3.1).
            "vans": {
                "count": len(fleet),
                "capacity_kg": min(v.capacity_kg for v in fleet),
                "capacity_crates": min(v.capacity_crates for v in fleet),
            },
            "slots": [
                {
                    "id": str(s.id),
                    "window_start_s": solver.seconds(s.start),
                    "window_end_s": solver.seconds(s.end),
                }
                for s in slots
            ],
        }
    )


async def _expected(
    session: AsyncSession,
    expected: dict[tuple[uuid.UUID, uuid.UUID], int],
    slots: list[orders_api.SlotAvailability],
) -> dict[str, Stop]:
    """One stop per (society, active box slot) with boxes: a crate per three, 8 kg a box."""
    windows = {s.id: (solver.seconds(s.start), solver.seconds(s.end)) for s in slots}
    points = await community_api.society_points(session, sorted({s for s, _ in expected}))
    return {
        f"{society_id}/{slot_id}": Stop(
            id=f"{society_id}/{slot_id}",
            society_id=society_id,
            slot_id=slot_id,
            point=points[society_id],
            window=windows[slot_id],
            orders=boxes,
            crates=math.ceil(boxes / solver.BAGS_PER_CRATE),
            kg_tenths=boxes * TENTATIVE_BOX_KG_TENTHS,
        )
        for (society_id, slot_id), boxes in sorted(expected.items())
        if boxes > 0 and society_id in points and slot_id in windows
    }


async def _published(
    session: AsyncSession, plan_id: uuid.UUID, vehicles: list[Vehicle]
) -> tuple[dict[str, Stop], list[tuple[int, list[str]]]]:
    """The plan's drops and trips in departure and stop order. A route on a vehicle no longer
    active, and a society without a location, are left out."""
    van_of = {v.id: i for i, v in enumerate(vehicles)}
    rows = (
        await session.execute(
            select(Route.id, Route.vehicle_id, RouteStop)
            .join(RouteStop, RouteStop.route_id == Route.id)
            .where(Route.plan_id == plan_id, Route.status != "cancelled", RouteStop.kind == "drop")
            .order_by(Route.depart_time, Route.trip, Route.id, RouteStop.seq)
        )
    ).all()
    societies = sorted({r.society_id for _, _, r in rows if r.society_id is not None})
    points = await community_api.society_points(session, societies)
    stops: dict[str, Stop] = {}
    trips: dict[uuid.UUID, tuple[int, list[str]]] = {}
    for route_id, vehicle_id, r in rows:
        society_id, slot_id, start, end = r.society_id, r.slot_id, r.window_start, r.window_end
        if society_id is None or slot_id is None or start is None or end is None:
            continue
        if vehicle_id not in van_of or society_id not in points:
            continue
        stop_id = f"{society_id}/{slot_id}"
        stops[stop_id] = Stop(
            id=stop_id,
            society_id=society_id,
            slot_id=slot_id,
            point=points[society_id],
            window=(solver.seconds(start), solver.seconds(end)),
            orders=r.orders,
            crates=r.crates,
            kg_tenths=r.kg_tenths,
            empties=r.empties_expected,
        )
        trips.setdefault(route_id, (van_of[vehicle_id], []))[1].append(stop_id)
    return stops, list(trips.values())
