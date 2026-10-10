"""Facade of M9 Community & Kindness: the only module other modules may import.

M1 uses it to check a household's address (spec §3.1): the society exists, the tower belongs to
it, and the flat's pin lies inside its boundary. M7, M3 and M1 read captains through it (§3.2).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community.events import CAPTAIN_ASSIGNED, CAPTAIN_ENDED
from gharbasket.modules.community.models import Captain, Society
from gharbasket.modules.partners import api as partners_api
from gharbasket.platform.geo import geography

__all__ = [
    "CAPTAIN_ASSIGNED",
    "CAPTAIN_ENDED",
    "CaptainCard",
    "CaptainRef",
    "SocietyLabels",
    "SocietyRef",
    "captain_card",
    "captain_for_society",
    "contains",
    "export_for_user",
    "get_society",
    "hubs_with_societies",
    "is_active_captain",
    "society_labels",
]


@dataclass(frozen=True)
class SocietyRef:
    id: uuid.UUID
    hub_id: uuid.UUID | None
    status: str
    tower_ids: frozenset[str]
    live_at: datetime | None  # set once, when it first went live (§3.6, §3.8)


async def get_society(session: AsyncSession, society_id: uuid.UUID) -> SocietyRef | None:
    society = await session.get(Society, society_id)
    if society is None:
        return None
    return SocietyRef(
        id=society.id,
        hub_id=society.hub_id,
        status=society.status,
        tower_ids=frozenset(str(tower["id"]) for tower in society.towers),
        live_at=society.live_at,
    )


async def contains(session: AsyncSession, society_id: uuid.UUID, lat: float, lng: float) -> bool:
    """True if the point is inside (or on) the society's boundary, or no boundary is drawn yet.

    False for an unknown society.
    """
    inside = await session.execute(
        select(func.coalesce(func.ST_Covers(Society.boundary, geography(lat, lng)), True)).where(
            Society.id == society_id
        )
    )
    return bool(inside.scalar_one_or_none())


async def hubs_with_societies(session: AsyncSession) -> list[uuid.UUID]:
    """Every hub that serves at least one society (the generation job runs per hub)."""
    rows = await session.execute(
        select(Society.hub_id)
        .where(Society.hub_id.is_not(None))
        .distinct()
        .order_by(Society.hub_id)
    )
    return [hub_id for hub_id in rows.scalars() if hub_id is not None]


@dataclass(frozen=True)
class SocietyLabels:
    name: str
    towers: dict[str, str]  # tower id -> label ("a" -> "A wing")


async def society_labels(
    session: AsyncSession, society_ids: list[uuid.UUID]
) -> dict[uuid.UUID, SocietyLabels]:
    """Names and tower labels for packing queues and bag labels; unknown ids are left out."""
    if not society_ids:
        return {}
    rows = await session.execute(
        select(Society.id, Society.name, Society.towers).where(Society.id.in_(society_ids))
    )
    return {
        society_id: SocietyLabels(name, {str(t["id"]): str(t["label"]) for t in towers})
        for society_id, name, towers in rows.all()
    }


# --- captains (§3.2, ADR-0026 §4) ---------------------------------------------------------------


@dataclass(frozen=True)
class CaptainRef:
    captain_id: uuid.UUID
    user_id: uuid.UUID
    partner_id: uuid.UUID
    display_name: str | None


@dataclass(frozen=True)
class CaptainCard:
    """What a resident sees of her society's captain: never a flat or a phone."""

    display_name: str
    photo_url: str | None


async def _active(session: AsyncSession, society_id: uuid.UUID) -> Captain | None:
    return (
        await session.execute(
            select(Captain).where(Captain.society_id == society_id, Captain.status == "active")
        )
    ).scalar_one_or_none()


async def captain_for_society(session: AsyncSession, society_id: uuid.UUID) -> CaptainRef | None:
    """The society's active captain (M7's handover), or None."""
    captain = await _active(session, society_id)
    if captain is None:
        return None
    card = await partners_api.profile_card(session, captain.partner_id)
    return CaptainRef(
        captain_id=captain.id,
        user_id=captain.user_id,
        partner_id=captain.partner_id,
        display_name=card.display_name if card else None,
    )


async def captain_card(session: AsyncSession, society_id: uuid.UUID) -> CaptainCard | None:
    """The active captain's card, for residents of this society only (the caller checks)."""
    captain = await _active(session, society_id)
    if captain is None:
        return None
    card = await partners_api.profile_card(session, captain.partner_id)
    return None if card is None else CaptainCard(card.display_name, card.photo_url)


async def is_active_captain(
    session: AsyncSession, user_id: uuid.UUID, society_id: uuid.UUID
) -> bool:
    """For M7's permission check and M1's role consumer (ADR-0026 §2)."""
    found = await session.execute(
        select(Captain.id).where(
            Captain.user_id == user_id,
            Captain.society_id == society_id,
            Captain.status == "active",
        )
    )
    return found.first() is not None


async def export_for_user(session: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
    """M9's section of the user's data export (M1 §3.3): her own captain appointments."""
    rows = await session.execute(
        select(Captain, Society.name)
        .join(Society, Society.id == Captain.society_id)
        .where(Captain.user_id == user_id)
        .order_by(Captain.since, Captain.id)
    )
    return {
        "captain_appointments": [
            {
                "society_id": captain.society_id,
                "society": name,
                "status": captain.status,
                "since": captain.since,
                "until": captain.until,
            }
            for captain, name in rows.all()
        ]
    }
