"""Facade of M7 Logistics: the only module other modules may import.

M1 exports and erases through it; M12 pulls `route_facts` for its analytics refresh (M12 §3.1) and
reads `today_routes` for its today board (M12 §3.3). M9 and M6 consume `PICKUP_COMPLETED` (§3.6).
"""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.logistics import privacy
from gharbasket.modules.logistics.events import PICKUP_COMPLETED
from gharbasket.modules.logistics.facts import RouteFact, route_facts
from gharbasket.modules.logistics.today import TodayRoutes, today_routes

__all__ = [
    "PICKUP_COMPLETED",
    "RouteFact",
    "TodayRoutes",
    "anonymise_for_user",
    "export_for_user",
    "route_facts",
    "today_routes",
]


async def export_for_user(session: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
    """M7's section of the user's data export (M1 §3.3), read-only: the household's deliveries
    (status, proof kind, times; no photos or codes) and, for a rider, the routes, handovers and GPS
    pings of the last 90 days."""
    return await privacy.export(session, user_id)


async def anonymise_for_user(
    session: AsyncSession, user_id: uuid.UUID, household_id: uuid.UUID | None
) -> dict[str, int]:
    """M1's purge (M1 §3.5, order 6): the rider's pings and the household's door photos and OTP
    records go; user ids on handovers, deliveries, field events, routes and vehicles are nulled
    (the order history keeps the actor kind). Returns {table: rows} for the tombstone."""
    return await privacy.anonymise(session, user_id, household_id)
