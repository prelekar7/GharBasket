"""Facade of M1 Identity & Household: the only module other modules may import."""

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity.models import Address, Household, HouseholdMember, User


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


@dataclass(frozen=True)
class HouseholdPlanProfile:
    """What the weekly planner needs to know about a household (M3 §3.1, M10 §3.1)."""

    id: uuid.UUID
    owner_user_id: uuid.UUID
    society_id: uuid.UUID
    hub_id: uuid.UUID | None
    size: int
    diet: str
    jain: bool
    diabetic: bool
    cuisines: tuple[str, ...]
    weekday_cook_minutes: int
    weekend_cook_minutes: int
    no_cook_days: tuple[int, ...]
    budget_paise: int | None
    fasts: frozenset[str]
    no_onion_garlic_on_fasts: bool
    member_age_bands: tuple[str, ...]
    autopilot_enabled: bool
    auto_approve: bool
    data_source: str = "real"  # ADR-0008: what the household's cycles and orders inherit


async def household_plan_profile(
    session: AsyncSession, household_id: uuid.UUID
) -> HouseholdPlanProfile | None:
    household = await session.get(Household, household_id)
    if household is None:
        return None
    members = list(
        (
            await session.execute(
                select(HouseholdMember).where(HouseholdMember.household_id == household_id)
            )
        ).scalars()
    )
    fasts: set[str] = set()
    strict = False
    jain = False
    for m in members:
        fasts |= set(m.fasting_rules.get("fasts", []))
        strict = strict or bool(m.fasting_rules.get("no_onion_garlic_on_fast_days"))
        jain = jain or bool(m.fasting_rules.get("jain"))
    return HouseholdPlanProfile(
        id=household.id,
        owner_user_id=household.owner_user_id,
        society_id=household.society_id,
        hub_id=household.hub_id,
        size=household.size,
        diet=household.diet,
        jain=jain,
        diabetic=any("diabetes" in m.health_tags for m in members),
        cuisines=tuple(household.cuisine_tags),
        weekday_cook_minutes=household.weekday_cook_minutes,
        weekend_cook_minutes=household.weekend_cook_minutes,
        no_cook_days=tuple(household.no_cook_days),
        budget_paise=household.budget_paise,
        fasts=frozenset(fasts),
        no_onion_garlic_on_fasts=strict,
        member_age_bands=tuple(m.age_band for m in members),
        autopilot_enabled=household.autopilot_enabled,
        auto_approve=household.auto_approve,
        data_source=household.data_source,
    )


async def households_of_hub(session: AsyncSession, hub_id: uuid.UUID) -> list[uuid.UUID]:
    """Households served by this hub with Autopilot on, oldest first (the generation job)."""
    rows = await session.execute(
        select(Household.id)
        .where(Household.hub_id == hub_id, Household.autopilot_enabled)
        .order_by(Household.id)
    )
    return list(rows.scalars())


@dataclass(frozen=True)
class PrivateTerms:
    """The household's own words that must not leave the API in free text (M13's scrub, DPDP):
    the owner's name (whole and each part) and the flat and tower of the address."""

    names: tuple[str, ...]
    places: tuple[str, ...]


async def private_terms(session: AsyncSession, household_id: uuid.UUID) -> PrivateTerms:
    row = (
        await session.execute(
            select(User.name, Address.flat, Address.tower_id)
            .select_from(Household)
            .join(User, User.id == Household.owner_user_id)
            .outerjoin(Address, Address.household_id == Household.id)
            .where(Household.id == household_id)
        )
    ).first()
    if row is None:
        return PrivateTerms((), ())
    name, flat, tower = row
    names = (name, *name.split()) if name else ()
    return PrivateTerms(names, tuple(p for p in (flat, tower) if p))
