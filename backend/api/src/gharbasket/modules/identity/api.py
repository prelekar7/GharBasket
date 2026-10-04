"""Facade of M1 Identity & Household: the only module other modules may import."""

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity.models import Household


async def households_in_society(session: AsyncSession, society_id: uuid.UUID) -> int:
    """Households whose address is in this society: the unlock count (M9 US-2)."""
    count = await session.execute(
        select(func.count()).select_from(Household).where(Household.society_id == society_id)
    )
    return count.scalar_one()


async def household_hub(session: AsyncSession, user_id: uuid.UUID) -> uuid.UUID | None:
    """The hub that serves this user's household (for prices, M2); None without one."""
    hub = await session.execute(select(Household.hub_id).where(Household.owner_user_id == user_id))
    return hub.scalar_one_or_none()


@dataclass(frozen=True)
class HouseholdRef:
    id: uuid.UUID
    owner_user_id: uuid.UUID
    society_id: uuid.UUID
    hub_id: uuid.UUID | None


async def household_for_user(session: AsyncSession, user_id: uuid.UUID) -> HouseholdRef | None:
    """The household this user owns (orders, cycles); None before onboarding."""
    row = (
        await session.execute(
            select(
                Household.id, Household.owner_user_id, Household.society_id, Household.hub_id
            ).where(Household.owner_user_id == user_id)
        )
    ).first()
    return None if row is None else HouseholdRef(*row)


async def owner_of_household(session: AsyncSession, household_id: uuid.UUID) -> uuid.UUID | None:
    """The user whose wallet pays for this household's boxes."""
    return (
        await session.execute(select(Household.owner_user_id).where(Household.id == household_id))
    ).scalar_one_or_none()
