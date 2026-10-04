"""Facade of M3 Plans & Subscriptions: the only module other modules may import."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.subscriptions import service
from gharbasket.modules.subscriptions.models import WeeklyCycle

__all__ = ["WeeklyCycle", "cycle_for", "mark_locked"]


async def cycle_for(
    session: AsyncSession, household_id: uuid.UUID, delivery_date
) -> WeeklyCycle | None:
    return await service.cycle_for(session, household_id, delivery_date)


async def mark_locked(session: AsyncSession, cycle_id: uuid.UUID, order_id: uuid.UUID) -> None:
    """M4 locked the order at the cut-off: mirror it on the cycle (consumer of orders.locked)."""
    cycle = await session.get(WeeklyCycle, cycle_id)
    if cycle is not None and cycle.status in ("approved", "awaiting_approval"):
        cycle.status = "locked"
        cycle.order_id = order_id
        cycle.version += 1
        await session.flush()
