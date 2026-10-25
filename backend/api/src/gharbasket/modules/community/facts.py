"""Impact fact rows of M9 for M12's analytics refresh (M12 §3.1): verified donations, NGO hand-offs
(`handoff_items`, `handoff_food`, `handoff_meals`), donated-meal pledges (`meals_pledged`) and
societies going live, one row each, with ids, counts and points only (no name, flat, phone, photo
or note). Referrals come from `referral_facts`. Hand-offs and pledges belong to a hub, not a
society (`society_id` None); a donation row keeps `ngo_id` None (its hand-off row names the NGO).
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community.drives import IST, half_up
from gharbasket.modules.community.models import Donation, MealPledge, NgoHandoff, Society


@dataclass(frozen=True)
class ImpactFact:
    source: str  # donation | handoff | pledge | society
    source_id: uuid.UUID
    kind: str  # donation_verified | handoff_items | handoff_food | handoff_meals | meals_pledged
    #            | society_live
    hub_id: uuid.UUID | None
    society_id: uuid.UUID | None
    ngo_id: uuid.UUID | None
    on: date  # IST: verified, handed over, pledged, or went live
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
    """Donations verified, hand-offs made, meals pledged and societies that went live between the
    two IST dates (inclusive), in that order, each kind in id order."""
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
    facts += await _gift_facts(session, start, end, hub_ids)
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


async def _gift_facts(
    session: AsyncSession, start: datetime, end: datetime, hub_ids: list[uuid.UUID] | None
) -> list[ImpactFact]:
    """Hand-offs by `handed_at`, then pledges by `pledged_at`."""
    handoffs = select(NgoHandoff).where(NgoHandoff.handed_at >= start, NgoHandoff.handed_at <= end)
    pledges = select(MealPledge).where(MealPledge.pledged_at >= start, MealPledge.pledged_at <= end)
    if hub_ids is not None:
        handoffs = handoffs.where(NgoHandoff.hub_id.in_(hub_ids))
        pledges = pledges.where(MealPledge.hub_id.in_(hub_ids))
    facts = [
        ImpactFact(
            source="handoff",
            source_id=h.id,
            kind=f"handoff_{h.kind}",
            hub_id=h.hub_id,
            society_id=None,
            ngo_id=h.ngo_id,
            on=h.handed_at.astimezone(IST).date(),
            items=h.items_count,
            kg_tenths=h.kg_tenths,
            meals=h.meals,
            points=0,
            people_reached=h.people_reached,
            household_id=None,
            data_source=h.data_source,
        )
        for h in (await session.execute(handoffs.order_by(NgoHandoff.id))).scalars()
    ]
    facts += [
        ImpactFact(
            source="pledge",
            source_id=p.id,
            kind="meals_pledged",
            hub_id=p.hub_id,
            society_id=None,
            ngo_id=None,
            on=p.pledged_at.astimezone(IST).date(),
            items=0,
            kg_tenths=0,
            meals=p.meals,
            points=0,
            people_reached=None,
            household_id=None,
            data_source=p.data_source,
        )
        for p in (await session.execute(pledges.order_by(MealPledge.id))).scalars()
    ]
    return facts
