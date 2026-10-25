"""What M12's today board needs from M7 (M12 §3.3): the hub's routes, stops and delivery attempts
for one day, as counts. Ids, timestamps and numbers only; no rider, society or flat."""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.logistics.models import STARTED, Delivery, Route, RoutePlan, RouteStop

IST = ZoneInfo("Asia/Kolkata")
UNDELIVERED = ("undelivered", "redelivery", "refunded", "triage")  # attempts that failed


@dataclass(frozen=True)
class TodayRoutes:
    planned: int  # routes of published plans, not cancelled
    started: int  # in progress or completed
    completed: int
    stops_total: int  # drop stops
    stops_done: int  # handed over or skipped
    late_stops: int  # handed over after the window's end, or still open past it
    at_society: int  # attempts with the captain
    delivered: int
    held: int
    undelivered: int  # failed attempts: undelivered, redelivery, refunded, triage
    plans_to_review: int  # solved, not published (M7 §3.2: `logistics.plan_needs_review`)
    oldest_plan_at: datetime | None  # when the oldest of them was made


async def today_routes(
    session: AsyncSession, *, hub_id: uuid.UUID, on: date, now: datetime | None = None
) -> TodayRoutes:
    """Routes, stops and delivery attempts of the hub on the IST day `on`. A stop is late when it
    was handed over after its window's end (IST), or is still open and `now` is past it."""
    now = now or datetime.now(UTC)
    routes = (
        await session.execute(
            select(Route.id, Route.status)
            .join(RoutePlan, RoutePlan.id == Route.plan_id)
            .where(
                Route.hub_id == hub_id,
                Route.delivery_date == on,
                RoutePlan.status == "published",
                Route.status != "cancelled",
            )
        )
    ).all()
    stops = (
        await session.execute(
            select(RouteStop.status, RouteStop.window_end, RouteStop.handed_over_at).where(
                RouteStop.route_id.in_([route_id for route_id, _ in routes]),
                RouteStop.kind == "drop",
            )
        )
    ).all()
    late = 0
    for status, window_end, handed_over_at in stops:
        if window_end is None or status == "skipped":
            continue
        due = datetime.combine(on, window_end, IST)
        if (handed_over_at if handed_over_at is not None else now) > due:
            late += 1
    attempts = dict(
        (
            await session.execute(
                select(Delivery.status, func.count())
                .where(Delivery.hub_id == hub_id, Delivery.delivery_date == on)
                .group_by(Delivery.status)
            )
        ).all()
    )
    review_count, review_oldest = (
        await session.execute(
            select(func.count(), func.min(RoutePlan.created_at)).where(
                RoutePlan.hub_id == hub_id,
                RoutePlan.delivery_date == on,
                RoutePlan.status == "solved",
            )
        )
    ).one()
    return TodayRoutes(
        planned=len(routes),
        started=sum(1 for _, status in routes if status in STARTED),
        completed=sum(1 for _, status in routes if status == "completed"),
        stops_total=len(stops),
        stops_done=sum(1 for status, *_ in stops if status in ("handed_over", "skipped")),
        late_stops=late,
        at_society=int(attempts.get("with_captain", 0)),
        delivered=int(attempts.get("delivered", 0)),
        held=int(attempts.get("held", 0)),
        undelivered=sum(int(n) for status, n in attempts.items() if status in UNDELIVERED),
        plans_to_review=int(review_count),
        oldest_plan_at=review_oldest,
    )
