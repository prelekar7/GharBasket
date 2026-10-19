"""Facade of M8 Partner Network: the only module other modules may import (spec, "Facade").

M9 checks a captain through `get_partner` and builds her card from `profile_card`; M7 reads
partners at handover; M1 puts `export_for_user` into the data export and turns the events below
into roles.
"""

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.partners import applications, kyc, service, training
from gharbasket.modules.partners.events import (
    APPLICATION_SUBMITTED,
    REINSTATED,
    SUSPENDED,
    VERIFIED,
)
from gharbasket.modules.partners.models import Partner

__all__ = [
    "APPLICATION_SUBMITTED",
    "REINSTATED",
    "SUSPENDED",
    "VERIFIED",
    "PartnerRef",
    "ProfileCard",
    "export_for_user",
    "get_partner",
    "partner_for_user",
    "profile_card",
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


async def export_for_user(session: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
    """M8's section of the user's data export (M1 §3.3), read-only: her applications, her KYC
    documents as masked references with their check and review states (no files, hashes or
    encrypted numbers), her training attempts and her partner records, without staff-only fields
    (the suspension note, who decided or reviewed). Ratings join it when they ship (S6)."""
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
    }
