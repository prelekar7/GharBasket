"""What M7 answers about routes and stops (spec §3.3): `RouteSummary`, `Stop` and `RouteDetail`.

Names come from M1 (riders, for hub staff), society names and points from M9, slots from M4. The
handover code is shown only while the route is in progress; only the route's rider and hub staff
can read a route at all.
"""

import uuid
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community import api as community_api
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.logistics import pickups, plans, solver
from gharbasket.modules.logistics.models import (
    Delivery,
    Route,
    RoutePlan,
    RouteStop,
    StopOrder,
    booked,
)
from gharbasket.modules.logistics.routing import clock
from gharbasket.modules.logistics.schemas import (
    CaptainOut,
    KitchenOut,
    LatLngOut,
    RiderRef,
    RouteDetail,
    RouteSummary,
    StopOut,
    StopSlot,
    StopSociety,
    TiffinItem,
    VehicleRef,
)
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.partners import api as partners_api

IST = ZoneInfo("Asia/Kolkata")
MAPS = "https://www.google.com/maps/dir/?api=1&destination={lat:.5f},{lng:.5f}"


def hhmm(at: Any) -> str:
    return at.strftime("%H:%M")


def ist_seconds(at: datetime) -> int:
    local = at.astimezone(IST)
    return local.hour * 3600 + local.minute * 60 + local.second


def now() -> datetime:
    return datetime.now(UTC)


def route_summary(row: plans.RouteRow, names: dict[uuid.UUID, str]) -> RouteSummary:
    route = row.route
    rider = route.rider_user_id
    return RouteSummary(
        id=route.id,
        plan_id=route.plan_id,
        hub_id=route.hub_id,
        delivery_date=route.delivery_date,
        kind=route.kind,  # pyright: ignore[reportArgumentType]
        trip=route.trip,
        status=route.status,  # pyright: ignore[reportArgumentType]
        vehicle=VehicleRef(
            id=row.vehicle.id,
            label=row.vehicle.label,
            kind=row.vehicle.kind,  # pyright: ignore[reportArgumentType]
        ),
        rider=None if rider is None else RiderRef(user_id=rider, name=names.get(rider)),
        depart=hhmm(route.depart_time),
        return_by=hhmm(route.return_by),
        stops_total=row.stops_total,
        stops_done=row.stops_done,
        crates=row.crates,
        bags=row.bags,
        km=route.planned_km,
        started_at=route.started_at,
        completed_at=route.completed_at,
        version=route.version,
    )


async def summaries(session: AsyncSession, rows: list[plans.RouteRow]) -> list[RouteSummary]:
    riders = sorted({r.route.rider_user_id for r in rows if r.route.rider_user_id is not None})
    names = await identity_api.user_names(session, riders)
    return [route_summary(r, names) for r in rows]


async def stops_of(session: AsyncSession, route_id: uuid.UUID) -> list[RouteStop]:
    found = await session.execute(
        select(RouteStop).where(RouteStop.route_id == route_id).order_by(RouteStop.seq)
    )
    return list(found.scalars())


def eta_now(route: Route, stops: list[RouteStop]) -> dict[uuid.UUID, str | None]:
    """After start, each open stop's planned ETA plus the delay at the last stop reached (§3.3);
    a stop already reached shows when it was reached. Null before start and for closed stops."""
    out: dict[uuid.UUID, str | None] = {}
    delay = 0
    for stop in stops:
        if stop.arrived_at is not None:
            delay = stop.delay_s
        running = route.status == "in_progress" and stop.status in ("pending", "arrived")
        out[stop.id] = clock(solver.seconds(stop.eta) + delay) if running else None
    return out


async def stop_views(
    session: AsyncSession, route: Route, stops: list[RouteStop], *, kitchens: bool = False
) -> list[StopOut]:
    """The stops as the route's rider sees them, or hub staff (`kitchens`: a hub manager or an
    admin, who see a kitchen's address and point always, §3.7)."""
    societies = sorted({s.society_id for s in stops if s.society_id is not None})
    labels = await community_api.society_labels(session, societies)
    points = await community_api.society_points(session, societies)
    captains = {sid: await community_api.captain_card(session, sid) for sid in societies}
    slots = {
        slot_id: await orders_api.slot_ref(session, slot_id)
        for slot_id in {s.slot_id for s in stops if s.slot_id is not None}
    }
    back = await _redelivery_bags(session, [s.id for s in stops])
    etas = eta_now(route, stops)
    taken_back = await pickups.stop_pickups(session, route, stops)
    places, tiffins = await _tiffin_parts(session, route, stops, kitchens)
    out = []
    for stop in stops:
        society = slot = point = card = None
        kitchen = places.get(stop.id)
        if kitchen is not None and kitchen.location is not None:
            point = (kitchen.location.lat, kitchen.location.lng)
        if stop.society_id is not None:
            point = points.get(stop.society_id)
            label = labels.get(stop.society_id)
            society = StopSociety(
                id=stop.society_id,
                name=None if label is None else label.name,
                location=None if point is None else LatLngOut(lat=point[0], lng=point[1]),
            )
            card = captains[stop.society_id]
        ref = None if stop.slot_id is None else slots.get(stop.slot_id)
        if ref is not None:
            slot = StopSlot(id=ref.id, label=ref.label, start=hhmm(ref.start), end=hhmm(ref.end))
        out.append(
            StopOut(
                id=stop.id,
                seq=stop.seq,
                kind=stop.kind,  # pyright: ignore[reportArgumentType]
                society=society,
                slot=slot,
                eta=hhmm(stop.eta),
                late_min=stop.late_s // 60,
                eta_now=etas[stop.id],
                status=stop.status,  # pyright: ignore[reportArgumentType]
                crates=stop.crates,
                bags=stop.bags,
                redelivery_bags=back.get(stop.id, 0),
                empties_expected=stop.empties_expected,
                pickups=taken_back.get(stop.id),
                captain=None if card is None else CaptainOut(**vars(card)),
                rider_mode=stop.rider_mode,
                handover_code=stop.handover_code if route.status == "in_progress" else None,
                navigate_url=None if point is None else MAPS.format(lat=point[0], lng=point[1]),
                arrived_at=stop.arrived_at,
                handed_over_at=stop.handed_over_at,
                kitchen=kitchen,
                portions=stop.portions,
                tiffins=tiffins.get(stop.id),
            )
        )
    return out


TIFFIN_STATUS = {
    "on_route": "on_route",
    "with_captain": "with_captain",
    "delivered": "delivered",
    "undelivered": "undelivered",
    "refunded": "undelivered",
    "triage": "undelivered",
}


async def _tiffin_parts(
    session: AsyncSession, route: Route, stops: list[RouteStop], staff: bool
) -> tuple[dict[uuid.UUID, KitchenOut], dict[uuid.UUID, list[TiffinItem]]]:
    """A tiffin route's kitchens and each stop's tiffins (§3.7). The kitchen's address and point
    go to the route's rider from the start until every tiffin of his route at that kitchen is
    collected, and to hub managers and admins (`staff`); flat labels only on rider-mode drops of
    a route in progress."""
    if route.kind != "tiffin":
        return {}, {}
    places = await partners_api.kitchen_places(
        session, sorted({s.partner_id for s in stops if s.partner_id is not None})
    )
    rows = list(
        (
            await session.execute(
                select(StopOrder)
                .where(StopOrder.stop_id.in_([s.id for s in stops]))
                .order_by(StopOrder.short_code)
            )
        ).scalars()
    )
    tried: dict[uuid.UUID, Delivery] = {}
    found = await session.execute(
        select(Delivery)
        .where(Delivery.order_id.in_({r.order_id for r in rows}))
        .order_by(Delivery.attempt)
    )
    for attempt in found.scalars():
        tried[attempt.order_id] = attempt
    waiting = {r.stop_id for r in rows if r.order_id not in tried}  # stops with a tiffin to collect
    kitchens: dict[uuid.UUID, KitchenOut] = {}
    for stop in stops:
        place = None if stop.partner_id is None else places.get(stop.partner_id)
        if place is None or stop.window_start is None:
            continue
        shown = staff or (
            route.status == "in_progress" and (stop.status != "handed_over" or stop.id in waiting)
        )
        located = shown and place.lat is not None and place.lng is not None
        kitchens[stop.id] = KitchenOut(
            partner_id=place.partner_id,
            display_name=place.display_name,
            area=place.area,
            ready_by=hhmm(stop.window_start),
            address=place.address if shown else None,
            location=LatLngOut(lat=place.lat, lng=place.lng) if located else None,  # pyright: ignore[reportArgumentType]
        )
    by_id = {s.id: s for s in stops}
    labelled = {
        r.order_id
        for r in rows
        if by_id[r.stop_id].rider_mode
        and by_id[r.stop_id].kind == "drop"
        and route.status == "in_progress"
    }
    labels = await _flat_labels(session, sorted(labelled))
    tiffins: dict[uuid.UUID, list[TiffinItem]] = {s.id: [] for s in stops}
    for row in rows:
        attempt = tried.get(row.order_id)
        status = (
            "to_collect" if route.status in ("draft", "published", "in_progress") else "left_behind"
        )
        if attempt is not None:
            status = (
                "left_behind"
                if attempt.reason == "left_behind"
                else TIFFIN_STATUS.get(attempt.status, "undelivered")
            )
        tiffins[row.stop_id].append(
            TiffinItem(
                order_id=row.order_id,
                short_code=row.short_code,
                portions=row.portions,
                status=status,  # pyright: ignore[reportArgumentType]
                flat_label=labels.get(row.order_id),
            )
        )
    return kitchens, tiffins


async def _flat_labels(
    session: AsyncSession, order_ids: list[uuid.UUID]
) -> dict[uuid.UUID, str | None]:
    """Tower and flat of each order's household, as the captain's checklist shows them."""
    orders = [await orders_api.get(session, order_id) for order_id in order_ids]
    labels = await identity_api.delivery_labels(session, [o.household_id for o in orders])
    towers = await community_api.society_labels(session, sorted({o.society_id for o in orders}))
    out: dict[uuid.UUID, str | None] = {}
    for order in orders:
        label = labels.get(order.household_id)
        society = towers.get(order.society_id)
        if label is None:
            out[order.id] = None
            continue
        tower = society.towers.get(label.tower_id, label.tower_id) if society else label.tower_id
        out[order.id] = f"{tower}, {label.flat}"
    return out


async def route_detail(
    session: AsyncSession, route: Route, *, kitchens: bool = False
) -> RouteDetail:
    [row] = await plans.route_rows(session, Route.id == route.id)
    [summary] = await summaries(session, [row])
    plan = await session.get(RoutePlan, route.plan_id)
    return RouteDetail(
        **summary.model_dump(),
        stops=await stop_views(
            session, route, await stops_of(session, route.id), kitchens=kitchens
        ),
        source=None if plan is None else plan.source,  # pyright: ignore[reportArgumentType]
        left_behind=list(route.left_behind),
    )


async def _redelivery_bags(
    session: AsyncSession, stop_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    """Redelivery bags the van carries per stop (back from the hub; not those a captain kept)."""
    if not stop_ids:
        return {}
    rows = await session.execute(
        select(Delivery.stop_id, func.count())
        .where(
            Delivery.stop_id.in_(stop_ids),
            booked(),
            Delivery.status != "refunded",
            Delivery.kept_by_captain.is_(False),
        )
        .group_by(Delivery.stop_id)
    )
    return {stop_id: int(n) for stop_id, n in rows.all() if stop_id is not None}
