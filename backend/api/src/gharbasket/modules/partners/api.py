"""Facade of M8 Partner Network: the only module other modules may import (spec, "Facade").

M9 checks a captain through `get_partner` and builds her card from `profile_card`; M7 reads
partners at handover; M1 puts `export_for_user` into the data export and turns the events below
into roles. M12 pulls `partner_facts` and `assignment_run_facts` for its analytics refresh
(M12 §3.1).
"""

import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.partners import applications, kyc, pickups, ratings, service, training
from gharbasket.modules.partners.events import (
    APPLICATION_DECIDED,
    APPLICATION_SUBMITTED,
    ASSIGNMENT_COMPLETED,
    ASSIGNMENT_CREATED,
    ASSIGNMENT_RELEASED,
    RATING_REQUESTED,
    REINSTATED,
    SUSPENDED,
    TIFFINS_UNASSIGNED,
    VERIFIED,
)
from gharbasket.modules.partners.facts import (
    AssignmentRunFact,
    PartnerFact,
    assignment_run_facts,
    partner_facts,
)
from gharbasket.modules.partners.models import Application, Partner
from gharbasket.modules.partners.pickups import PickupRef

__all__ = [
    "APPLICATION_DECIDED",
    "APPLICATION_SUBMITTED",
    "ASSIGNMENT_COMPLETED",
    "ASSIGNMENT_CREATED",
    "ASSIGNMENT_RELEASED",
    "RATING_REQUESTED",
    "REINSTATED",
    "SUSPENDED",
    "TIFFINS_UNASSIGNED",
    "VERIFIED",
    "AssignmentRunFact",
    "PartnerFact",
    "PartnerRef",
    "PickupRef",
    "ProfileCard",
    "anonymise_for_user",
    "application_reason",
    "assignment_run_facts",
    "export_for_user",
    "get_partner",
    "partner_facts",
    "partner_for_user",
    "pickup",
    "profile_card",
    "tiffin_pickups",
]


# Never in an export: the files, the encrypted number and its key, the hash, the reviewer.
KYC_PRIVATE = frozenset(
    {"file_keys", "number_enc", "key_version", "number_hash", "reviewed_by", "user_id"}
)


@dataclass(frozen=True)
class PartnerRef:
    id: uuid.UUID
    user_id: uuid.UUID
    kind: str
    hub_id: uuid.UUID
    status: str
    display_name: str


@dataclass(frozen=True)
class ProfileCard:
    """What customers may see of a partner: never an address, phone or KYC detail."""

    display_name: str
    photo_url: str | None


def _ref(partner: Partner) -> PartnerRef:
    return PartnerRef(
        id=partner.id,
        user_id=partner.user_id,
        kind=partner.kind,
        hub_id=partner.hub_id,
        status=partner.status,
        display_name=partner.display_name,
    )


async def get_partner(session: AsyncSession, partner_id: uuid.UUID) -> PartnerRef | None:
    partner = await session.get(Partner, partner_id)
    return None if partner is None else _ref(partner)


async def partner_for_user(
    session: AsyncSession, user_id: uuid.UUID, kind: str
) -> PartnerRef | None:
    partner = (
        await session.execute(
            select(Partner).where(Partner.user_id == user_id, Partner.kind == kind)
        )
    ).scalar_one_or_none()
    return None if partner is None else _ref(partner)


async def profile_card(session: AsyncSession, partner_id: uuid.UUID) -> ProfileCard | None:
    """The public face of a partner (M9's captain card). `photo_url` is null until a
    partner_photo upload purpose exists (§11)."""
    partner = await session.get(Partner, partner_id)
    return None if partner is None else ProfileCard(partner.display_name, None)


async def tiffin_pickups(
    session: AsyncSession, hub_id: uuid.UUID, on_date: date, meal: str
) -> list[PickupRef]:
    """M7's tiffin routes: the hub's kitchens with tiffins to collect for a date and meal."""
    return await pickups.tiffin_pickups(session, hub_id, on_date, meal)


async def pickup(session: AsyncSession, pickup_qr: str, hub_id: uuid.UUID) -> PickupRef:
    """M7's rider scan: the orders ready at the kitchen whose `GB1A:` code this is. Raises 404
    `partners.pickup_invalid` for an unknown, malformed or other hub's code."""
    return await pickups.pickup(session, pickup_qr, hub_id)


async def application_reason(session: AsyncSession, application_id: uuid.UUID) -> str | None:
    """Why an application was rejected (M11's notification of `partners.application_decided`;
    the event carries no text). For `resubmit`, the reasons are on the documents."""
    application = await session.get(Application, application_id)
    return None if application is None else application.reason


async def anonymise_for_user(
    session: AsyncSession, user_id: uuid.UUID, household_id: uuid.UUID | None
) -> dict[str, int]:
    """M1's purge (M1 §3.5): the ratings she gave lose her user id and comment, and keep the
    score, so a kitchen's rating is unchanged. Applications, KYC and partner records are not
    touched here."""
    return {"ratings": await ratings.erase_for_user(session, user_id)}


async def export_for_user(session: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
    """M8's section of the user's data export (M1 §3.3), read-only: her applications, her KYC
    documents as masked references with their check and review states (no files, hashes or
    encrypted numbers), her training attempts, her partner records, without staff-only fields
    (the suspension note, who decided or reviewed), and the ratings she gave."""
    return {
        "applications": [
            application.as_dict(exclude={"created_by", "decided_by", "data_source"})
            for application in await applications.for_user(session, user_id)
        ],
        "kyc_documents": [
            document.as_dict(exclude=KYC_PRIVATE)
            for document in await kyc.all_for_user(session, user_id)
        ],
        "training_attempts": [
            attempt.as_dict(exclude={"user_id"})
            for attempt in await training.attempts_for_user(session, user_id)
        ],
        "partners": [
            partner.as_dict(exclude={"suspend_note", "photo_key", "data_source"})
            for partner in await service.for_user(session, user_id)
        ],
        "ratings": [
            rating.as_dict(exclude={"user_id"})
            for rating in await ratings.given_by(session, user_id)
        ],
    }
