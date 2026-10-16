"""Facade of M9 Community & Kindness: the only module other modules may import.

M1 uses it to check a household's address (spec §3.1): the society exists, the tower belongs to
it, and the flat's pin lies inside its boundary. M7, M3 and M1 read captains through it (§3.2).
M4 reads `live_at` for the pre-live box fee (§3.6), and M12 the public society page (§3.8).
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

from geoalchemy2 import Geometry
from sqlalchemy import cast, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community import donations
from gharbasket.modules.community.events import (
    CAPTAIN_ASSIGNED,
    CAPTAIN_ENDED,
    DONATION_CANCELLED,
    DONATION_REJECTED,
    DONATION_SCHEDULED,
    DONATION_VERIFIED,
    REFERRAL_CONVERTED,
    SOCIETY_LIVE,
)
from gharbasket.modules.community.models import Captain, Society, SocietyRequest
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.partners import api as partners_api
from gharbasket.platform.geo import geography

__all__ = [
    "CAPTAIN_ASSIGNED",
    "CAPTAIN_ENDED",
    "DONATION_CANCELLED",
    "DONATION_REJECTED",
    "DONATION_SCHEDULED",
    "DONATION_VERIFIED",
    "REFERRAL_CONVERTED",
    "SOCIETY_LIVE",
    "CaptainCard",
    "CaptainRef",
    "DonationPickup",
    "PublicSociety",
    "SocietyLabels",
    "SocietyRef",
    "anonymise_for_user",
    "captain_card",
    "captain_for_society",
    "contains",
    "export_for_user",
    "get_society",
    "hubs_with_societies",
    "is_active_captain",
    "pickups_for",
    "public_society",
    "record_pickup",
    "society_labels",
    "society_points",
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


async def society_points(
    session: AsyncSession, society_ids: list[uuid.UUID]
) -> dict[uuid.UUID, tuple[float, float]]:
    """Where a van stops for each society, as (lat, lng) (M7's route stops): a point inside its
    boundary until M9 records a gate. Societies without a boundary are left out."""
    if not society_ids:
        return {}
    inside = func.ST_PointOnSurface(cast(Society.boundary, Geometry))
    rows = await session.execute(
        select(Society.id, func.ST_Y(inside), func.ST_X(inside)).where(
            Society.id.in_(society_ids), Society.boundary.is_not(None)
        )
    )
    return {society_id: (float(lat), float(lng)) for society_id, lat, lng in rows.all()}


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


# --- donation pickups (§3.4, §3.8): M7's return leg and the captain's flat checklist -----------


@dataclass(frozen=True)
class DonationPickup:
    donation_id: uuid.UUID
    household_id: uuid.UUID
    items_summary: str  # "clothes 2.5 kg, toys 3": no names, flats or photos


async def pickups_for(
    session: AsyncSession, society_id: uuid.UUID, on: date
) -> list[DonationPickup]:
    """The society's donations to collect on `on`; M7 adds the flat (identity.api)."""
    return [
        DonationPickup(
            donation_id=d.id,
            household_id=d.household_id,
            items_summary=", ".join(
                f"{line['code']} {line['qty']:g}{' kg' if line['unit'] == 'kg' else ''}"
                for line in d.items
            ),
        )
        for d in await donations.pickups_for(session, society_id, on)
    ]


async def record_pickup(
    session: AsyncSession,
    donation_id: uuid.UUID,
    outcome: Literal["collected", "not_ready"],
    *,
    by_user_id: uuid.UUID | None,
    on: date,
) -> bool:
    """One donation's outcome from a stop (M7's `logistics.pickup_completed`, §3.4), in the
    caller's transaction: `collected` makes it `picked_up` (by the rider or captain, on `on`);
    `not_ready` rolls it once to the next delivery date, then cancels it. False when it was no
    longer scheduled (a redelivered report), so calling twice is harmless."""
    done = await donations.record_pickup(
        session, donation_id, outcome, by_user_id=by_user_id, on=on
    )
    return done is not None


async def export_for_user(session: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
    """M9's section of the user's data export (M1 §3.3): her captain appointments and her
    donations (photos counted, not included)."""
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
        ],
        "donations": await donations.export(session, user_id),
    }


@dataclass(frozen=True)
class PublicSociety:
    id: uuid.UUID
    slug: str
    name: str
    area: str | None
    status: str
    joined: int
    threshold: int
    live_at: datetime | None


async def public_society(session: AsyncSession, slug: str) -> PublicSociety | None:
    """The referral landing page's society (M12 §3.7): `unlocking` and `live` ones only."""
    society = (
        await session.execute(select(Society).where(Society.slug == slug))
    ).scalar_one_or_none()
    if society is None or society.status not in ("unlocking", "live"):
        return None
    return PublicSociety(
        id=society.id,
        slug=society.slug,
        name=society.name,
        area=society.area,
        status=society.status,
        joined=await identity_api.households_in_society(session, society.id),
        threshold=society.unlock_threshold,
        live_at=society.live_at,
    )


async def anonymise_for_user(
    session: AsyncSession, user_id: uuid.UUID, household_id: uuid.UUID | None = None
) -> dict[str, int]:
    """M1's purge (M1 §3.5, order 4; §3.8 here): the user's society requests (name, area, pin,
    note) are deleted. The fuller eraser of §3.8 (referral codes, referrals, donations) replaces
    this when it ships."""
    requests = await session.execute(
        delete(SocietyRequest).where(SocietyRequest.user_id == user_id)
    )
    return {"society_requests": requests.rowcount}  # pyright: ignore[reportAttributeAccessIssue]
