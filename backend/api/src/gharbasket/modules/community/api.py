"""Facade of M9 Community & Kindness: the only module other modules may import.

M1 uses it to check a household's address (spec §3.1): the society exists, the tower belongs to
it, and the flat's pin lies inside its boundary. M7, M3 and M1 read captains through it (§3.2).
M4 reads `live_at` for the pre-live box fee (§3.6); M12 reads the public society page (§3.8) and
its societies table (`hub_societies`, M12 §3.3), and pulls `impact_facts` (donations, hand-offs,
meal pledges, societies gone live) and `referral_facts` for its analytics refresh (M12 §3.1); M1
exports and erases through it.
"""

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal

from geoalchemy2 import Geometry
from sqlalchemy import cast, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community import donations, impact
from gharbasket.modules.community.events import (
    CAPTAIN_ASSIGNED,
    CAPTAIN_ENDED,
    DONATION_CANCELLED,
    DONATION_REJECTED,
    DONATION_SCHEDULED,
    DONATION_VERIFIED,
    HANDOFF_RECORDED,
    REFERRAL_CONVERTED,
    SOCIETY_LIVE,
)
from gharbasket.modules.community.facts import ImpactFact, impact_facts
from gharbasket.modules.community.impact import HubImpact, SocietyImpact
from gharbasket.modules.community.models import (
    Captain,
    MealPledge,
    Ngo,
    Referral,
    ReferralCode,
    Society,
    SocietyRequest,
)
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
    "HANDOFF_RECORDED",
    "REFERRAL_CONVERTED",
    "SOCIETY_LIVE",
    "CaptainCard",
    "CaptainRef",
    "DonationPickup",
    "HubImpact",
    "HubSociety",
    "ImpactFact",
    "NgoRef",
    "PublicSociety",
    "ReferralFact",
    "SocietyImpact",
    "SocietyLabels",
    "SocietyRef",
    "anonymise_for_user",
    "captain_card",
    "captain_for_society",
    "contains",
    "export_for_user",
    "get_society",
    "hub_societies",
    "hub_society_ids",
    "hubs_with_societies",
    "impact_facts",
    "is_active_captain",
    "ngo_refs",
    "pickups_for",
    "public_society",
    "record_pickup",
    "referral_facts",
    "society_impact",
    "society_labels",
    "society_points",
]


@dataclass(frozen=True)
class SocietyRef:
    id: uuid.UUID
    hub_id: uuid.UUID | None
    status: str
    tower_ids: frozenset[str]
    name: str
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
        name=society.name,
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


async def hub_society_ids(session: AsyncSession, hub_id: uuid.UUID) -> list[uuid.UUID]:
    """Every society the hub serves, whatever its status (M3's cut-off monitor, §3.4)."""
    rows = await session.execute(
        select(Society.id).where(Society.hub_id == hub_id).order_by(Society.id)
    )
    return list(rows.scalars())


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
    longer scheduled, or a `not_ready` already rolled it past `on` (a redelivered report), so
    calling twice is harmless."""
    done = await donations.record_pickup(
        session, donation_id, outcome, by_user_id=by_user_id, on=on
    )
    return done is not None


async def export_for_user(session: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
    """M9's section of the user's data export (M1 §3.3, §3.8): her captain appointments, her
    donations (photos counted, not included), her referral code, the referrals she made (never the
    neighbour), whether she was referred, and her donated-meal pledges."""
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
        **await _referrals_export(session, user_id),
        "meal_pledges": [
            {"meals": p.meals, "served": p.served, "pledged_at": p.pledged_at}
            for p in (
                await session.execute(
                    select(MealPledge).where(MealPledge.user_id == user_id).order_by(MealPledge.id)
                )
            ).scalars()
        ],
    }


def _referral_row(referral: Referral, points: int) -> dict[str, Any]:
    return {
        "status": referral.status,
        "joined_on": referral.joined_on,
        "converted_on": referral.converted_on,
        "points": points if referral.status == "converted" else None,
    }


async def _referrals_export(session: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
    code = await session.get(ReferralCode, user_id)
    made = await session.execute(
        select(Referral).where(Referral.referrer_user_id == user_id).order_by(Referral.id)
    )
    referred = (
        await session.execute(select(Referral).where(Referral.referee_user_id == user_id))
    ).scalar_one_or_none()
    return {
        "referral_code": None if code is None else code.code,
        "referrals_made": [_referral_row(row, row.referrer_points) for row in made.scalars()],
        "referred": None if referred is None else _referral_row(referred, referred.referee_points),
    }


async def anonymise_for_user(
    session: AsyncSession, user_id: uuid.UUID, household_id: uuid.UUID | None = None
) -> dict[str, int]:
    """M1's purge (M1 §3.5, §3.8 here): the referral code and society requests are deleted;
    referrals are kept for the impact numbers, open ones voided, and their user ids no longer
    lead to a person. Meal pledges are kept as they are: they hold no personal field."""
    codes = await session.execute(delete(ReferralCode).where(ReferralCode.user_id == user_id))
    requests = await session.execute(
        delete(SocietyRequest).where(SocietyRequest.user_id == user_id)
    )
    voided = await session.execute(
        update(Referral)
        .where(
            Referral.status == "joined",
            or_(Referral.referrer_user_id == user_id, Referral.referee_user_id == user_id),
        )
        .values(status="void", void_reason="deletion")
    )
    return {
        "referral_codes": codes.rowcount,  # pyright: ignore[reportAttributeAccessIssue]
        "society_requests": requests.rowcount,  # pyright: ignore[reportAttributeAccessIssue]
        "referrals": voided.rowcount,  # pyright: ignore[reportAttributeAccessIssue]
    }


@dataclass(frozen=True)
class ReferralFact:
    """A referral for M12's funnel (M12 §3.1): no user, name, flat or phone."""

    referral_id: uuid.UUID
    society_id: uuid.UUID
    hub_id: uuid.UUID | None
    status: str
    joined_on: date
    converted_on: date | None
    referrer_points: int | None
    referee_points: int | None
    data_source: str


async def referral_facts(
    session: AsyncSession,
    *,
    date_from: date,
    date_to: date,
    hub_ids: list[uuid.UUID] | None = None,
    by: Literal["joined", "converted"] = "joined",
) -> list[ReferralFact]:
    """Referrals that joined (or, with `by="converted"`, converted) between the two IST dates
    (inclusive), oldest first."""
    on = Referral.joined_on if by == "joined" else Referral.converted_on
    stmt = select(Referral).where(on >= date_from, on <= date_to)
    if hub_ids is not None:
        stmt = stmt.where(Referral.hub_id.in_(hub_ids))
    rows = await session.execute(stmt.order_by(Referral.id))
    return [
        ReferralFact(
            referral_id=row.id,
            society_id=row.society_id,
            hub_id=row.hub_id,
            status=row.status,
            joined_on=row.joined_on,
            converted_on=row.converted_on,
            referrer_points=row.referrer_points if row.status == "converted" else None,
            referee_points=row.referee_points if row.status == "converted" else None,
            data_source=row.data_source,
        )
        for row in rows.scalars()
    ]


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


@dataclass(frozen=True)
class HubSociety:
    id: uuid.UUID
    name: str
    status: str  # prospect | unlocking | live | paused
    joined: int  # households whose address is in it
    threshold: int  # households that unlock free delivery
    data_source: str  # real | synthetic


async def hub_societies(session: AsyncSession, *, hub_id: uuid.UUID) -> list[HubSociety]:
    """Every society the hub serves, whatever its status, by name (M12's societies table, §3.3):
    name, status and unlock progress; no captain, address or household."""
    societies = (
        (
            await session.execute(
                select(Society).where(Society.hub_id == hub_id).order_by(Society.name, Society.id)
            )
        )
        .scalars()
        .all()
    )
    joined = await identity_api.households_in_societies(session, [s.id for s in societies])
    return [
        HubSociety(
            id=society.id,
            name=society.name,
            status=society.status,
            joined=joined[society.id],
            threshold=society.unlock_threshold,
            data_source=society.data_source,
        )
        for society in societies
    ]


async def society_impact(
    session: AsyncSession, society_id: uuid.UUID, period: str = "all", *, with_hub: bool = True
) -> SocietyImpact | None:
    """The `society` scope of M9 §3.7 with the k rules applied (M12's public society page, §3.8):
    None for an unknown society. `period` is `all` or an IST month `YYYY-MM` within the last 12
    (anything else is 422 `request.invalid`). The numbers are live; `with_hub=False` leaves out
    the hub block (the public page does not use it)."""
    return await impact.society_impact(session, society_id, period, with_hub=with_hub)


@dataclass(frozen=True)
class NgoRef:
    """An NGO for M12's impact report: its name and the hubs it collects from."""

    id: uuid.UUID
    name: str
    hub_ids: tuple[uuid.UUID, ...]


async def ngo_refs(session: AsyncSession, ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, NgoRef]:
    """The NGOs among `ids` (unknown ids are left out). No coordinator, phone or registration."""
    wanted = set(ids)
    if not wanted:
        return {}
    rows = await session.execute(select(Ngo.id, Ngo.name, Ngo.hub_ids).where(Ngo.id.in_(wanted)))
    return {ngo_id: NgoRef(ngo_id, name, tuple(hubs)) for ngo_id, name, hubs in rows.all()}
