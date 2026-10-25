"""Facade of M1 Identity & Household: the only module other modules may import."""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, cast, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity import events, households, service
from gharbasket.modules.identity.models import (
    Address,
    Consent,
    Household,
    HouseholdMember,
    LoginSession,
    User,
    UserRole,
)

# Events other modules consume (M9: the unlock count).
HOUSEHOLD_CREATED = events.HOUSEHOLD_CREATED
HOUSEHOLD_UPDATED = events.HOUSEHOLD_UPDATED
# Events other modules consume (spec §3.5): the deletion request, its cancelling, the purge.
DELETION_REQUESTED = events.DELETION_REQUESTED
DELETION_CANCELLED = events.DELETION_CANCELLED
USER_DELETED = events.USER_DELETED


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
    data_source: str = "real"


async def household_for_user(session: AsyncSession, user_id: uuid.UUID) -> HouseholdRef | None:
    """The household this user owns (orders, cycles); None before onboarding."""
    row = (
        await session.execute(
            select(
                Household.id,
                Household.owner_user_id,
                Household.society_id,
                Household.hub_id,
                Household.data_source,
            ).where(Household.owner_user_id == user_id)
        )
    ).first()
    return None if row is None else HouseholdRef(*row)


async def signed_up_at(session: AsyncSession, user_id: uuid.UUID) -> datetime | None:
    """When the user first signed in (M9: the 14-day referral claim window)."""
    return (
        await session.execute(select(User.created_at).where(User.id == user_id))
    ).scalar_one_or_none()


@dataclass(frozen=True)
class DeliveryLabel:
    tower_id: str
    flat: str


async def delivery_labels(
    session: AsyncSession, household_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, DeliveryLabel]:
    """Tower and flat per household, for bag labels and hub queues (ADR-0026): never a name
    or a phone. Households without an address are left out."""
    ids = set(household_ids)
    if not ids:
        return {}
    rows = await session.execute(
        select(Address.household_id, Address.tower_id, Address.flat).where(
            Address.household_id.in_(ids)
        )
    )
    return {household_id: DeliveryLabel(tower, flat) for household_id, tower, flat in rows.all()}


@dataclass(frozen=True)
class UserContact:
    name: str | None
    phone_e164: str


async def user_contacts(
    session: AsyncSession, user_ids: list[uuid.UUID]
) -> dict[uuid.UUID, UserContact]:
    """Names and phones for hub staff screens (M8 PartnerStaff); never for customers."""
    if not user_ids:
        return {}
    rows = await session.execute(
        select(User.id, User.name, User.phone_e164).where(User.id.in_(set(user_ids)))
    )
    return {user_id: UserContact(name, phone) for user_id, name, phone in rows.all()}


async def user_by_phone(session: AsyncSession, phone_e164: str) -> uuid.UUID | None:
    """The signed-up user with this phone (M8: a hub manager creates a rider or packer
    application for a person without a household); None when nobody signed up with it."""
    return (
        await session.execute(
            select(User.id).where(User.phone_e164 == phone_e164, User.status == "active")
        )
    ).scalar_one_or_none()


async def owner_of_household(session: AsyncSession, household_id: uuid.UUID) -> uuid.UUID | None:
    """The user whose wallet pays for this household's boxes."""
    return (
        await session.execute(select(Household.owner_user_id).where(Household.id == household_id))
    ).scalar_one_or_none()


@dataclass(frozen=True)
class Contact:
    name: str | None
    phone_e164: str


async def contact_for_household(session: AsyncSession, household_id: uuid.UUID) -> Contact | None:
    """The owner's name and phone, for M4's issue triage where the hub calls the customer back
    (M4 §3.6). The caller audit-logs each read."""
    row = (
        await session.execute(
            select(User.name, User.phone_e164)
            .join(Household, Household.owner_user_id == User.id)
            .where(Household.id == household_id)
        )
    ).first()
    return None if row is None else Contact(*row)


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


async def shortage_preferences(
    session: AsyncSession, household_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """Each household's choice for a short item, "substitute" or "refund" (spec §3.4), for M6's
    packing queue and M4's mark_shortage. Read when the shortage is recorded, so a change applies
    from the moment it is saved; an unknown id reads as "substitute"."""
    ids = set(household_ids)
    if not ids:
        return {}
    rows = await session.execute(
        select(Household.id, Household.shortage_preference).where(Household.id.in_(ids))
    )
    found: dict[uuid.UUID, str] = {household_id: pref for household_id, pref in rows}
    return {household_id: found.get(household_id, "substitute") for household_id in ids}


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


async def user_locale(session: AsyncSession, user_id: uuid.UUID) -> str:
    """en-IN, hi-IN or mr-IN: the language of pushes and WhatsApp messages (M11)."""
    locale = await session.scalar(select(User.locale).where(User.id == user_id))
    return locale or "en-IN"


async def user_phone(session: AsyncSession, user_id: uuid.UUID) -> str | None:
    """The E.164 phone WhatsApp messages go to (M11); None for an unknown user."""
    return await session.scalar(select(User.phone_e164).where(User.id == user_id))


@dataclass(frozen=True)
class PushTarget:
    session_id: uuid.UUID
    token: str
    platform: str


async def active_push_tokens(session: AsyncSession, user_id: uuid.UUID) -> list[PushTarget]:
    """The FCM tokens of the user's signed-in devices (neither revoked nor expired)."""
    return [
        PushTarget(device.id, device.push_token, device.platform)
        for device in await service.list_devices(session, user_id)
        if device.push_token
    ]


async def clear_push_token(session: AsyncSession, token: str) -> None:
    """FCM says the token is gone (the app was uninstalled): stop sending to it."""
    await session.execute(
        update(LoginSession).where(LoginSession.push_token == token).values(push_token=None)
    )


async def has_consent(session: AsyncSession, user_id: uuid.UUID, purpose: str) -> bool:
    """Whether the user has an active grant for this purpose (e.g. `whatsapp`, M11)."""
    return purpose in await households.active_consents(session, user_id)


async def export_for_user(session: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
    """M1's section of the user's data export (spec §3.3), read-only: no tokens, and health tags
    only while health_personalisation is granted."""
    user = await session.get(User, user_id)
    if user is None:
        return {}

    async def rows(model: Any, *where: Any) -> list[Any]:
        return list(
            (await session.execute(select(model).where(*where).order_by(model.id))).scalars()
        )

    household = await households.household_of(session, user_id)
    return {
        "user": user.as_dict(exclude={"data_source"}),
        "roles": [role.as_dict() for role in await rows(UserRole, UserRole.user_id == user_id)],
        "devices": [  # no push token, no refresh token
            device.as_dict(exclude={"push_token"})
            for device in await rows(LoginSession, LoginSession.user_id == user_id)
        ],
        "household": None
        if household is None
        else (await households.view(session, household)).model_dump(mode="json"),
        "consents": [c.as_dict() for c in await rows(Consent, Consent.user_id == user_id)],
    }


async def user_names(session: AsyncSession, user_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, str]:
    """Names of staff for "edited by" and history in the console (M2 §3.3); unnamed users and
    unknown ids are left out."""
    wanted = set(user_ids)
    if not wanted:
        return {}
    rows = await session.execute(select(User.id, User.name).where(User.id.in_(wanted)))
    return {user_id: name for user_id, name in rows.all() if name}


async def holds_role(
    session: AsyncSession, user_id: uuid.UUID, role: str, scope_id: uuid.UUID
) -> bool:
    """True if an active user holds a verified `role` scoped to exactly `scope_id` (a society,
    hub or NGO): M7 checks that a vehicle's default rider rides for its hub. A blocked or deleted
    user holds nothing."""
    query = (
        select(UserRole.id)
        .join(User, User.id == UserRole.user_id)
        .where(
            UserRole.user_id == user_id,
            UserRole.role == role,
            UserRole.scope_id == scope_id,
            UserRole.status == "verified",
            User.status == "active",
        )
    )
    return (await session.execute(query.limit(1))).first() is not None


async def is_active(session: AsyncSession, user_id: uuid.UUID) -> bool:
    """False while the account's deletion is scheduled, once it is purged, and for a blocked
    account (spec §3.5): M11 checks it before sending anything."""
    return await session.scalar(select(User.status).where(User.id == user_id)) == "active"


@dataclass(frozen=True)
class HouseholdFact:
    """A household for M12's cohorts (M12 §3.1): ids, the IST signup date and the owner's account
    status; never a name, phone, flat or health field."""

    household_id: uuid.UUID
    hub_id: uuid.UUID | None
    society_id: uuid.UUID
    created_on: date  # IST
    status: str  # the owner's account (active | blocked | deleting): a household has no status
    data_source: str


async def household_facts(
    session: AsyncSession,
    *,
    created_from: date | None = None,
    ids: Iterable[uuid.UUID] | None = None,
) -> list[HouseholdFact]:
    """Households created on or after the IST date `created_from` (every one when None), or only
    `ids`, in id order. A purged household has no row, so it leaves M12's facts at its next full
    pull."""
    created_on = cast(func.timezone("Asia/Kolkata", Household.created_at), Date)
    stmt = select(
        Household.id,
        Household.hub_id,
        Household.society_id,
        created_on,
        User.status,
        Household.data_source,
    ).join(User, User.id == Household.owner_user_id)
    if created_from is not None:
        stmt = stmt.where(created_on >= created_from)
    if ids is not None:
        stmt = stmt.where(Household.id.in_(set(ids)))
    rows = await session.execute(stmt.order_by(Household.id))
    return [HouseholdFact(*row) for row in rows.all()]
