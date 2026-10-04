"""Facade of M1 Identity & Household: the only module other modules may import."""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity.models import Household


async def households_in_society(session: AsyncSession, society_id: uuid.UUID) -> int:
    """Households whose address is in this society: the unlock count (M9 US-2)."""
    count = await session.execute(
        select(func.count()).select_from(Household).where(Household.society_id == society_id)
    )
    return count.scalar_one()
