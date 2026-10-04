"""Member and address edits after onboarding (M1 spec §3.2).

Each edit locks the household row, so concurrent edits queue up; it then bumps the household's
version and publishes one `identity.household_updated` event, like a household PATCH.
"""

import uuid
from typing import Any

import h3
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community import api as community_api
from gharbasket.modules.identity.households import (
    H3_RESOLUTION,
    address_out,
    bump_version,
    check_address,
    check_diet,
    health_consent_required,
    household_of,
    members_of,
    new_member,
    owned,
    shows_health,
)
from gharbasket.modules.identity.models import Address, Household, HouseholdMember
from gharbasket.modules.identity.schemas import AddressOut, AddressUpdate, MemberIn, MemberUpdate
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.geo import point

MAX_MEMBERS = 12


def _member_not_found() -> ProblemError:
    return ProblemError(404, "identity.member_not_found", "No such member in your household.")


async def _member(
    session: AsyncSession, household: Household, member_id: uuid.UUID
) -> HouseholdMember:
    member = await session.get(HouseholdMember, member_id)
    if member is None or member.household_id != household.id:
        raise _member_not_found()
    return member


async def add_member(
    session: AsyncSession, owner_id: uuid.UUID, household_id: uuid.UUID, body: MemberIn
) -> tuple[Household, HouseholdMember]:
    household = await owned(session, owner_id, household_id, for_update=True)
    members = await members_of(session, household.id)
    if len(members) >= MAX_MEMBERS:
        raise ProblemError(
            422, "identity.household_full", f"A household has at most {MAX_MEMBERS} members."
        )
    check_diet(household.diet, [body.fasting_rules.jain])
    if body.health_tags and not await shows_health(session, household):
        raise health_consent_required()
    member = new_member(household.id, body)
    session.add(member)
    household.size = len(members) + 1
    await session.flush()
    await session.refresh(member)
    await bump_version(session, household, ["members", "size"])
    return household, member


async def update_member(
    session: AsyncSession,
    owner_id: uuid.UUID,
    household_id: uuid.UUID,
    member_id: uuid.UUID,
    body: MemberUpdate,
) -> tuple[Household, HouseholdMember]:
    household = await owned(session, owner_id, household_id, for_update=True)
    member = await _member(session, household, member_id)
    if body.fasting_rules is not None:
        check_diet(household.diet, [body.fasting_rules.jain])
    if body.health_tags and not await shows_health(session, household):
        raise health_consent_required()

    changes: dict[str, Any] = {
        field: value
        for field, value in body.model_dump(exclude_unset=True).items()
        if getattr(member, field) != value
    }
    if not changes:
        return household, member
    for field, value in changes.items():
        setattr(member, field, value)
    await session.flush()
    await bump_version(session, household, ["members"])
    return household, member


async def remove_member(
    session: AsyncSession, owner_id: uuid.UUID, household_id: uuid.UUID, member_id: uuid.UUID
) -> Household:
    household = await owned(session, owner_id, household_id, for_update=True)
    member = await _member(session, household, member_id)
    if household.size <= 1:
        raise ProblemError(
            422, "identity.household_needs_member", "A household needs at least one member."
        )
    await session.delete(member)
    household.size -= 1
    await session.flush()
    await bump_version(session, household, ["members", "size"])
    return household


# --- address ----------------------------------------------------------------------------------


async def my_addresses(session: AsyncSession, owner_id: uuid.UUID) -> list[AddressOut]:
    """At most one in v1: the household's address, made at onboarding."""
    household = await household_of(session, owner_id)
    return [] if household is None else [await address_out(session, household.id)]


async def update_address(
    session: AsyncSession, owner_id: uuid.UUID, address_id: uuid.UUID, body: AddressUpdate
) -> Household:
    """Edit the address; moving to another society moves the household (and its hub) with it."""
    household = await household_of(session, owner_id, for_update=True)
    address = None if household is None else await session.get(Address, address_id)
    if household is None or address is None or address.household_id != household.id:
        raise ProblemError(404, "identity.address_not_found", "No such address.")

    current = await address_out(session, household.id)
    sent = body.model_fields_set
    society_id = body.society_id if body.society_id is not None else current.society_id
    tower_id = body.tower_id if body.tower_id is not None else current.tower_id
    location = body.location if "location" in sent else current.location
    flat = body.flat if body.flat is not None else current.flat

    moved = society_id != current.society_id
    placed = tower_id != current.tower_id or location != current.location
    if not (moved or placed or flat != current.flat):
        return household
    if moved or placed:
        society = await community_api.get_society(session, society_id)
        if society is None:
            raise ProblemError(422, "identity.society_unknown", "No such society.")
        await check_address(session, society.id, society.tower_ids, tower_id, location)
        if moved:
            household.society_id, household.hub_id = society.id, society.hub_id

    address.society_id, address.tower_id, address.flat = society_id, tower_id, flat
    if location != current.location:
        address.location = None if location is None else point(location.lat, location.lng)
        address.h3_r9 = (
            None
            if location is None
            else h3.latlng_to_cell(location.lat, location.lng, H3_RESOLUTION)
        )
    await session.flush()
    await bump_version(session, household, ["address", "society_id"] if moved else ["address"])
    return household
