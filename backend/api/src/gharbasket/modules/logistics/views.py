"""What M7 answers about routes and stops (spec §3.3): `RouteSummary`, `Stop` and `RouteDetail`.

Names come from M1 (riders, for hub staff), society names and points from M9, slots from M4. The
handover code is shown only while the route is in progress; only the route's rider and hub staff
can read a route at all.
"""

import uuid
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community import api as community_api
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.logistics import plans
from gharbasket.modules.logistics.models import Route, RoutePlan, RouteStop
from gharbasket.modules.logistics.routing import clock
from gharbasket.modules.logistics.schemas import (
    CaptainOut,
    LatLngOut,
    RiderRef,
    RouteDetail,
    RouteSummary,
    StopOut,
    StopSlot,
    StopSociety,
    VehicleRef,
)
from gharbasket.modules.logistics.solver import seconds
from gharbasket.modules.orders import api as orders_api

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
        out[stop.id] = clock(seconds(stop.eta) + delay) if running else None
    return out


async def stop_views(session: AsyncSession, route: Route, stops: list[RouteStop]) -> list[StopOut]:
    societies = sorted({s.society_id for s in stops if s.society_id is not None})
    labels = await community_api.society_labels(session, societies)
    points = await community_api.society_points(session, societies)
    captains = {sid: await community_api.captain_card(session, sid) for sid in societies}
    slots = {
        slot_id: await orders_api.slot_ref(session, slot_id)
        for slot_id in {s.slot_id for s in stops if s.slot_id is not None}
    }
    etas = eta_now(route, stops)
    out = []
    for stop in stops:
        society = slot = point = card = None
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
                redelivery_bags=0,
                empties_expected=stop.empties_expected,
                captain=None if card is None else CaptainOut(**vars(card)),
                rider_mode=stop.rider_mode,
                handover_code=stop.handover_code if route.status == "in_progress" else None,
                navigate_url=None if point is None else MAPS.format(lat=point[0], lng=point[1]),
                arrived_at=stop.arrived_at,
                handed_over_at=None,
            )
        )
    return out


async def route_detail(session: AsyncSession, route: Route) -> RouteDetail:
    [row] = await plans.route_rows(session, Route.id == route.id)
    [summary] = await summaries(session, [row])
    plan = await session.get(RoutePlan, route.plan_id)
    return RouteDetail(
        **summary.model_dump(),
        stops=await stop_views(session, route, await stops_of(session, route.id)),
        source=None if plan is None else plan.source,  # pyright: ignore[reportArgumentType]
        left_behind=list(route.left_behind),
    )
