"""Facade of M1 Identity & Household: the only module other modules may import."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession


async def households_in_society(session: AsyncSession, society_id: uuid.UUID) -> int:
    """Households whose address is in this society: the unlock count (M9 US-2).

    Households arrive in feat/M1-households; until then no household has joined anywhere.
    """
    return 0
