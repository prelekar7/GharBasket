"""Fact rows of M7 for M12's analytics refresh (M12 §3.1): one row per route of a published plan,
with metres, paise and counts only (no rider, phone or flat).

- **km** is the planned distance: riders' actual distance is not recorded per route.
- **cost** is the plan's solved cost (M10's route cost model, or the fallback's own summary) shared
  out over its routes by planned km, so a plan's routes add up to its cost exactly. None when the
  plan has no cost in its summary.
- **On time** is a stop handed over by the end of its window on the route's day (IST); a stop not
  handed over yet is neither on time nor late.
- **Data source** is the hub's: routes mix orders, and the simulator never routes.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.hub_ops import api as hub_ops_api
from gharbasket.modules.logistics.models import Route, RoutePlan, RouteStop

IST = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class RouteFact:
    route_id: uuid.UUID
    hub_id: uuid.UUID
    on: date  # the delivery date
    kind: str  # box | tiffin
    status: str  # published | in_progress | completed
    km_m: int  # planned
    cost_paise: int | None
    orders: int
    stops: int  # drop stops
    on_time_stops: int
    late_stops: int
    source: str | None  # the plan's: ml | ml_fallback | fallback_previous | fallback_nearest
    data_source: str  # the hub's


def share(total: int, weights: list[float]) -> list[int]:
    """`total` split in proportion to `weights`, whole paise, the largest remainders first, so the
    parts add up to `total` (equal parts when every weight is 0)."""
    if not weights:
        return []
    if sum(weights) <= 0:
        weights = [1.0] * len(weights)
    exact = [total * w / sum(weights) for w in weights]
    parts = [int(x) for x in exact]
    order = sorted(range(len(exact)), key=lambda i: exact[i] - parts[i], reverse=True)
    for i in order[: total - sum(parts)]:
        parts[i] += 1
    return parts


async def route_facts(
    session: AsyncSession,
    *,
    date_from: date,
    date_to: date,
    hub_ids: list[uuid.UUID] | None = None,
) -> list[RouteFact]:
    """Routes of published plans delivering between the two IST dates (inclusive), not cancelled,
    in id order."""
    stmt = (
        select(Route, RoutePlan.summary, RoutePlan.source)
        .join(RoutePlan, RoutePlan.id == Route.plan_id)
        .where(
            RoutePlan.status == "published",
            Route.status != "cancelled",
            Route.delivery_date >= date_from,
            Route.delivery_date <= date_to,
        )
        .order_by(Route.id)
    )
    if hub_ids is not None:
        stmt = stmt.where(Route.hub_id.in_(hub_ids))
    routes = (await session.execute(stmt)).all()
    if not routes:
        return []
    stops = await session.execute(
        select(RouteStop).where(
            RouteStop.route_id.in_([route.id for route, *_ in routes]), RouteStop.kind == "drop"
        )
    )
    days = {route.id: route.delivery_date for route, *_ in routes}
    counts: dict[uuid.UUID, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    for stop in stops.scalars():
        row = counts[stop.route_id]
        row[0] += 1
        row[1] += stop.orders
        if stop.handed_over_at is not None and stop.window_end is not None:
            # By the window's end on the route's day: a hand-over the next morning is late.
            due = datetime.combine(days[stop.route_id], stop.window_end, IST)
            on_time = stop.handed_over_at <= due
            row[2 if on_time else 3] += 1
    by_plan: dict[uuid.UUID, list[Route]] = defaultdict(list)
    for route, *_ in routes:
        by_plan[route.plan_id].append(route)
    costs: dict[uuid.UUID, int | None] = {}
    for route, summary, _ in routes:
        if route.id in costs:
            continue
        plan_routes = by_plan[route.plan_id]
        total = (summary or {}).get("cost_paise")
        parts = (
            [None] * len(plan_routes)
            if total is None
            else share(int(total), [r.planned_km for r in plan_routes])
        )
        costs.update({r.id: part for r, part in zip(plan_routes, parts, strict=True)})
    sources = await hub_ops_api.hub_data_sources(session, {route.hub_id for route, *_ in routes})
    return [
        RouteFact(
            route_id=route.id,
            hub_id=route.hub_id,
            on=route.delivery_date,
            kind=route.kind,
            status=route.status,
            km_m=round(route.planned_km * 1000),
            cost_paise=costs[route.id],
            orders=counts[route.id][1],
            stops=counts[route.id][0],
            on_time_stops=counts[route.id][2],
            late_stops=counts[route.id][3],
            source=source,
            data_source=sources.get(route.hub_id, "real"),
        )
        for route, _, source in routes
    ]
