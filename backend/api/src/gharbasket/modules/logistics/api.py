"""Facade of M7 Logistics: the only module other modules may import."""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.logistics import privacy

__all__ = ["anonymise_for_user", "export_for_user"]


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
