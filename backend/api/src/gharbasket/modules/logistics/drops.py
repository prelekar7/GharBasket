"""What M6 needs to pack in loading order (M6 §3.2, facade `published_stop_order`): where each
society and slot rides in the date's published box plan. Ids, times and sequence numbers only."""

import uuid
from dataclasses import dataclass
from datetime import date, time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.logistics.models import Route, RoutePlan, RouteStop


@dataclass(frozen=True)
class StopRef:
    """A drop on a published route: the route, its trip and departure (IST), the stop's seq."""

    route_id: uuid.UUID
    trip: int
    stop_seq: int
    depart: time


async def published_stop_order(
    session: AsyncSession, hub_id: uuid.UUID, day: date
) -> dict[tuple[uuid.UUID, uuid.UUID], StopRef]:
    """The drops of the hub's published box plan for the date, by (society_id, slot_id).
    Cancelled routes are left out. A society and slot is on one route only: a plan with it on
    two never publishes (`logistics.plan_incomplete`)."""
    rows = await session.execute(
        select(
            RouteStop.society_id,
            RouteStop.slot_id,
            Route.id,
            Route.trip,
            RouteStop.seq,
            Route.depart_time,
        )
        .join(Route, Route.id == RouteStop.route_id)
        .join(RoutePlan, RoutePlan.id == Route.plan_id)
        .where(
            RoutePlan.hub_id == hub_id,
            RoutePlan.delivery_date == day,
            RoutePlan.kind == "box",
            RoutePlan.status == "published",
            Route.status != "cancelled",
            RouteStop.kind == "drop",
        )
    )
    return {
        (society_id, slot_id): StopRef(route_id, trip, seq, depart)
        for society_id, slot_id, route_id, trip, seq, depart in rows.all()
        if society_id is not None and slot_id is not None
    }
