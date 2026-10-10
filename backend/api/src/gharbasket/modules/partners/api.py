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

from gharbasket.modules.partners import service
from gharbasket.modules.partners.events import REINSTATED, SUSPENDED, VERIFIED
from gharbasket.modules.partners.models import Partner

__all__ = [
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
    """M8's section of the user's data export (M1 §3.3), read-only: her partner records, without
    the staff-only suspension note. Applications, masked KYC references, training and ratings
    join it when they ship (S6)."""
    return {
        "partners": [
            partner.as_dict(exclude={"suspend_note", "photo_key", "data_source"})
            for partner in await service.for_user(session, user_id)
        ]
    }
