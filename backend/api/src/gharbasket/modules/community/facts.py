"""Impact fact rows of M9 for M12's analytics refresh (M12 §3.1): verified donations and
societies going live, one row each, with ids, counts and points only (no name, flat, phone, photo
or note). Referrals come from `referral_facts`.

NGO hand-offs, donated-meal pledges and the meals served (M9 §3.5) are not built, so there are no
`handoff_*` or `meals_pledged` rows yet and `ngo_id` and `people_reached` are always None: a
donation reaches an NGO only through a hand-off.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community.drives import IST, half_up
from gharbasket.modules.community.models import Donation, Society


@dataclass(frozen=True)
class ImpactFact:
    source: str  # donation | society
    source_id: uuid.UUID
    kind: str  # donation_verified | society_live
    hub_id: uuid.UUID | None
    society_id: uuid.UUID
    ngo_id: uuid.UUID | None
    on: date  # IST: verified, or went live
    items: int  # pieces, and kilograms counted as one item per kg (half up)
    kg_tenths: int  # the kilograms counted
    meals: int
    points: int  # Kindness points awarded
    people_reached: int | None
    household_id: uuid.UUID | None  # the donating household (kept as M9 keeps it)
    data_source: str


def _bounds(date_from: date, date_to: date) -> tuple[datetime, datetime]:
    return datetime.combine(date_from, time.min, IST), datetime.combine(date_to, time.max, IST)


def _counted(donation: Donation) -> tuple[int, int]:
    pieces = sum(Decimal(str(line.get("counted") or 0)) for line in donation.items)
    kg = sum(
        Decimal(str(line.get("counted") or 0)) for line in donation.items if line["unit"] == "kg"
    )
    return half_up(Decimal(pieces)), half_up(Decimal(kg) * 10)


async def impact_facts(
    session: AsyncSession,
    *,
    date_from: date,
    date_to: date,
    hub_ids: list[uuid.UUID] | None = None,
) -> list[ImpactFact]:
    """Donations verified, and societies that went live, between the two IST dates (inclusive):
    donations first, each kind in id order."""
    start, end = _bounds(date_from, date_to)
    donations = select(Donation).where(
        Donation.status.in_(("verified", "handed_over")),
        Donation.verified_at >= start,
        Donation.verified_at <= end,
    )
    societies = select(Society).where(Society.live_at >= start, Society.live_at <= end)
    if hub_ids is not None:
        donations = donations.where(Donation.hub_id.in_(hub_ids))
        societies = societies.where(Society.hub_id.in_(hub_ids))
    facts: list[ImpactFact] = []
    for donation in (await session.execute(donations.order_by(Donation.id))).scalars():
        items, kg_tenths = _counted(donation)
        assert donation.verified_at is not None  # verified or handed over
        facts.append(
            ImpactFact(
                source="donation",
                source_id=donation.id,
                kind="donation_verified",
                hub_id=donation.hub_id,
                society_id=donation.society_id,
                ngo_id=None,
                on=donation.verified_at.astimezone(IST).date(),
                items=items,
                kg_tenths=kg_tenths,
                meals=0,
                points=donation.points_awarded or 0,
                people_reached=None,
                household_id=donation.household_id,
                data_source=donation.data_source,
            )
        )
    for society in (await session.execute(societies.order_by(Society.id))).scalars():
        assert society.live_at is not None
        facts.append(
            ImpactFact(
                source="society",
                source_id=society.id,
                kind="society_live",
                hub_id=society.hub_id,
                society_id=society.id,
                ngo_id=None,
                on=society.live_at.astimezone(IST).date(),
                items=0,
                kg_tenths=0,
                meals=0,
                points=0,
                people_reached=None,
                household_id=None,
                data_source=society.data_source,
            )
        )
    return facts
