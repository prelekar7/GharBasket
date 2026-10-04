"""Households, members, the address and consents (M1 spec §3.2).

Onboarding is one transaction: `create_household` checks everything first, then writes the
household, members, address, consents and the `identity.household_created` event together, so a
retry can never leave half a household.

Health tags are sensitive (DPDP): they are accepted only while the owner's `health_personalisation`
consent is active, hidden from reads without it, and erased when it is revoked.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

import h3
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community import api as community_api
from gharbasket.modules.identity import events
from gharbasket.modules.identity.models import (
    CONSENT_PURPOSES,
    Address,
    Consent,
    Household,
    HouseholdMember,
)
from gharbasket.modules.identity.schemas import (
    AddressOut,
    ConsentOut,
    HouseholdIn,
    HouseholdOut,
    HouseholdUpdate,
    MemberIn,
    MemberOut,
)
from gharbasket.platform import audit
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.geo import LatLng, point
from gharbasket.platform.outbox import publish

# The privacy notice version of every purpose; anything else means the app showed an old notice.
CURRENT_CONSENT_VERSION = "v1"
REQUIRED_PURPOSES = ("service_delivery", "personalisation")
HEALTH = "health_personalisation"
H3_RESOLUTION = 9


def household_not_found() -> ProblemError:
    return ProblemError(404, "identity.household_not_found", "No household found.")


# --- consents ---------------------------------------------------------------------------------


def check_consent_version(version: str) -> None:
    if version != CURRENT_CONSENT_VERSION:
        raise ProblemError(
            409,
            "identity.consent_version_stale",
            "This privacy notice has changed. Show the current one and ask again.",
            extra={"current_version": CURRENT_CONSENT_VERSION},
        )


async def active_consents(session: AsyncSession, user_id: uuid.UUID) -> dict[str, Consent]:
    rows = await session.execute(
        select(Consent).where(Consent.user_id == user_id, Consent.revoked_at.is_(None))
    )
    return {consent.purpose: consent for consent in rows.scalars()}


async def grant(session: AsyncSession, user_id: uuid.UUID, purpose: str) -> None:
    """Record a grant of the current notice; does nothing if one is already active."""
    active = (await active_consents(session, user_id)).get(purpose)
    if active is not None and active.version == CURRENT_CONSENT_VERSION:
        return
    if active is not None:
        active.revoked_at = datetime.now(UTC)
        await session.flush()
    session.add(Consent(user_id=user_id, purpose=purpose, version=CURRENT_CONSENT_VERSION))
    await session.flush()


async def revoke(session: AsyncSession, user_id: uuid.UUID, purpose: str) -> None:
    """End the active grant, if any. Revoking health_personalisation erases every health tag."""
    active = (await active_consents(session, user_id)).get(purpose)
    if active is None:
        return
    active.revoked_at = datetime.now(UTC)
    await session.flush()
    if purpose == HEALTH:
        await _erase_health_tags(session, user_id)


async def _erase_health_tags(session: AsyncSession, user_id: uuid.UUID) -> None:
    household = await household_of(session, user_id)
    if household is None:
        return
    erased = await session.execute(
        update(HouseholdMember)
        .where(
            HouseholdMember.household_id == household.id,
            func.cardinality(HouseholdMember.health_tags) > 0,
        )
        .values(health_tags=[])
    )
    await audit.record(
        session,
        actor_user_id=user_id,
        action="identity.health_tags_erased",
        entity_type="household",
        entity_id=str(household.id),
        before={"members_with_tags": erased.rowcount},  # pyright: ignore[reportAttributeAccessIssue]
    )


async def consents_view(session: AsyncSession, user_id: uuid.UUID) -> list[ConsentOut]:
    """One entry per purpose: the latest grant, or "never given"."""
    rows = await session.execute(
        select(Consent)
        .where(Consent.user_id == user_id)
        .order_by(Consent.purpose, Consent.granted_at.desc(), Consent.id.desc())
        .ext(distinct_on(Consent.purpose))
    )
    latest = {consent.purpose: consent for consent in rows.scalars()}
    out: list[ConsentOut] = []
    for purpose in CONSENT_PURPOSES:
        consent = latest.get(purpose)
        out.append(
            ConsentOut.model_validate(
                {
                    "purpose": purpose,
                    "version": consent.version if consent else CURRENT_CONSENT_VERSION,
                    "granted": consent is not None and consent.revoked_at is None,
                    "granted_at": consent.granted_at if consent else None,
                    "revoked_at": consent.revoked_at if consent else None,
                }
            )
        )
    return out


# --- households -------------------------------------------------------------------------------


async def household_of(
    session: AsyncSession, owner_id: uuid.UUID, *, for_update: bool = False
) -> Household | None:
    stmt = select(Household).where(Household.owner_user_id == owner_id)
    if for_update:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one_or_none()


async def owned(
    session: AsyncSession, owner_id: uuid.UUID, household_id: uuid.UUID, *, for_update: bool = False
) -> Household:
    """The caller's household with this id; 404 for anyone else's, so ids can't be probed."""
    household = await household_of(session, owner_id, for_update=for_update)
    if household is None or household.id != household_id:
        raise household_not_found()
    return household


def _check_diet(diet: str, members: list[MemberIn] | list[HouseholdMember]) -> None:
    for member in members:
        jain = (
            member.fasting_rules.jain
            if isinstance(member, MemberIn)
            else bool(member.fasting_rules.get("jain"))
        )
        if jain and diet != "veg":
            raise ProblemError(
                422, "identity.diet_conflict", "A Jain member needs a vegetarian household."
            )


async def create_household(
    session: AsyncSession, owner_id: uuid.UUID, body: HouseholdIn, *, data_source: str = "real"
) -> Household:
    existing = await household_of(session, owner_id)
    if existing is not None:
        raise _household_exists(existing.id)

    society = await community_api.get_society(session, body.society_id)
    if society is None:
        raise ProblemError(422, "identity.society_unknown", "No such society.")
    await _check_address(
        session, body.society_id, society.tower_ids, body.address.tower_id, body.address.location
    )
    _check_diet(body.diet, body.members)

    for consent in body.consents:
        check_consent_version(consent.version)
    granted = {consent.purpose for consent in body.consents if consent.granted}
    effective = granted | set(await active_consents(session, owner_id))
    if not set(REQUIRED_PURPOSES) <= effective:
        raise ProblemError(
            422,
            "identity.consent_required",
            "Onboarding needs consent for service delivery and personalisation.",
        )
    if HEALTH not in effective and any(member.health_tags for member in body.members):
        raise _health_consent_required()

    household = Household(
        owner_user_id=owner_id,
        society_id=society.id,
        hub_id=society.hub_id,
        size=len(body.members),
        data_source=data_source,
        **body.model_dump(include=set(_SETTINGS)),
    )
    try:
        # A savepoint, so a concurrent onboarding by the same owner becomes a clean 409.
        async with session.begin_nested():
            session.add(household)
            await session.flush()
    except IntegrityError:
        other = await household_of(session, owner_id)
        if other is None:
            raise
        raise _household_exists(other.id) from None

    session.add_all(_member(household.id, member) for member in body.members)
    location = body.address.location
    session.add(
        Address(
            household_id=household.id,
            society_id=society.id,
            tower_id=body.address.tower_id,
            flat=body.address.flat,
            location=None if location is None else point(location.lat, location.lng),
            h3_r9=None
            if location is None
            else h3.latlng_to_cell(location.lat, location.lng, H3_RESOLUTION),
        )
    )
    for purpose in CONSENT_PURPOSES:
        if purpose in granted:
            await grant(session, owner_id, purpose)
    await session.flush()
    await session.refresh(household)

    await publish(
        session,
        events.HOUSEHOLD_CREATED,
        {
            "household_id": str(household.id),
            "owner_user_id": str(owner_id),
            "society_id": str(household.society_id),
            "hub_id": None if household.hub_id is None else str(household.hub_id),
            "size": household.size,
            "budget_paise": household.budget_paise,
            "data_source": household.data_source,
        },
    )
    return household


_SETTINGS = (
    "diet",
    "cuisine_tags",
    "weekday_cook_minutes",
    "weekend_cook_minutes",
    "no_cook_days",
    "budget_paise",
)


def _household_exists(household_id: uuid.UUID) -> ProblemError:
    return ProblemError(
        409,
        "identity.household_exists",
        "You already have a household.",
        extra={"household_id": str(household_id)},
    )


def _health_consent_required() -> ProblemError:
    return ProblemError(
        403,
        "identity.health_consent_required",
        "Health details need the health personalisation consent first.",
    )


async def _check_address(
    session: AsyncSession,
    society_id: uuid.UUID,
    tower_ids: frozenset[str],
    tower_id: str,
    location: LatLng | None,
) -> None:
    if tower_id not in tower_ids:
        raise ProblemError(422, "identity.tower_unknown", "That tower is not in this society.")
    if location is not None and not await community_api.contains(
        session, society_id, location.lat, location.lng
    ):
        raise ProblemError(
            422, "identity.address_outside_society", "That pin is outside the society."
        )


def _member(household_id: uuid.UUID, member: MemberIn) -> HouseholdMember:
    return HouseholdMember(
        household_id=household_id,
        age_band=member.age_band,
        sex=member.sex,
        health_tags=list(member.health_tags),
        fasting_rules=member.fasting_rules.model_dump(),
    )


async def update_household(
    session: AsyncSession, household: Household, expected_version: int, body: HouseholdUpdate
) -> Household:
    """Apply a PATCH to a household locked FOR UPDATE; 412 if someone changed it meanwhile."""
    if household.version != expected_version:
        raise ProblemError(
            412,
            "identity.version_conflict",
            "This household changed since you loaded it. Reload and try again.",
        )
    changes: dict[str, Any] = {
        field: value
        for field, value in body.model_dump(exclude_unset=True).items()
        if getattr(household, field) != value
    }
    if "diet" in changes:
        members = await session.execute(
            select(HouseholdMember).where(HouseholdMember.household_id == household.id)
        )
        _check_diet(changes["diet"], list(members.scalars()))
    if not changes:
        return household
    for field, value in changes.items():
        setattr(household, field, value)
    household.version += 1
    await session.flush()
    await session.refresh(household)
    await publish(
        session,
        events.HOUSEHOLD_UPDATED,
        {
            "household_id": str(household.id),
            "version": household.version,
            "changed": sorted(changes),
        },
    )
    return household


async def view(session: AsyncSession, household: Household) -> HouseholdOut:
    """The household as the API returns it; health tags only while the consent is active."""
    show_health = HEALTH in await active_consents(session, household.owner_user_id)
    members = await session.execute(
        select(HouseholdMember)
        .where(HouseholdMember.household_id == household.id)
        .order_by(HouseholdMember.created_at, HouseholdMember.id)
    )
    address, lat, lng = (
        await session.execute(
            select(
                Address,
                func.ST_Y(func.geometry(Address.location)),
                func.ST_X(func.geometry(Address.location)),
            ).where(Address.household_id == household.id)
        )
    ).one()
    return HouseholdOut(
        id=household.id,
        version=household.version,
        society_id=household.society_id,
        hub_id=household.hub_id,
        size=household.size,
        diet=household.diet,  # pyright: ignore[reportArgumentType]
        cuisine_tags=household.cuisine_tags,  # pyright: ignore[reportArgumentType]
        weekday_cook_minutes=household.weekday_cook_minutes,
        weekend_cook_minutes=household.weekend_cook_minutes,
        no_cook_days=household.no_cook_days,
        budget_paise=household.budget_paise,
        autopilot_enabled=household.autopilot_enabled,
        auto_approve=household.auto_approve,
        members=[
            MemberOut.model_validate(
                {
                    "id": member.id,
                    "age_band": member.age_band,
                    "sex": member.sex,
                    "fasting_rules": member.fasting_rules,
                    "health_tags": member.health_tags if show_health else [],
                }
            )
            for member in members.scalars()
        ],
        address=AddressOut(
            id=address.id,
            household_id=address.household_id,
            society_id=address.society_id,
            tower_id=address.tower_id,
            flat=address.flat,
            location=None if lat is None else LatLng(lat=lat, lng=lng),
        ),
        created_at=household.created_at,
    )
