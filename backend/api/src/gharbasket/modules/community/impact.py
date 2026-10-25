"""Customers' impact numbers (M9 §3.7): a household's own giving (`me`) and a society's (`society`,
also `community.api.society_impact` for M12's public page).

- **Small numbers are not shown** (k = 5, handbook 13). A society's donation and meal numbers are
  `None` until at least 5 distinct households of the society contributed in the period: a verified
  donation, a converted referral (the household it brought in) or a donated meal. `homes_joined`
  is `None` under 5 households, and the hub's partner earnings under 5 paid partners.
- **Live, not a view.** The spec's `community.mv_society_impact` could not hold a donated meal's
  society (a pledge belongs to a user and a hub, and only M1 knows the user's society), so the
  numbers are computed on request from M9's tables and `identity.api.society_owners`; `as_of` is
  the time of the read.
- **Months.** `period` is `all` or an IST calendar month `YYYY-MM` within the last 12 (this one
  included).
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.community.drives import IST, today
from gharbasket.modules.community.facts import counted
from gharbasket.modules.community.models import (
    Donation,
    MealPledge,
    NgoHandoff,
    Referral,
    Society,
)
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.partners import api as partners_api
from gharbasket.platform.errors import ProblemError

MIN_CONTRIBUTORS = 5  # k: fewer distinct households could be read off a number
MONTHS = 12
VERIFIED = ("verified", "handed_over")
Band = Literal["5+", "under_5"]


@dataclass(frozen=True)
class DonatedLine:
    code: str
    unit: str
    qty: float  # accepted (counted) totals


@dataclass(frozen=True)
class MyImpact:
    items_donated: int
    kg_donated: float
    donations_verified: int
    points_earned: int
    meals_donated: int
    homes_invited: int
    donated: list[DonatedLine]


@dataclass(frozen=True)
class HubImpact:
    """What the society's hub did (hub-wide, not per society): food handed to NGOs, donated meals
    served, and the hub's partners' earnings."""

    food_saved_kg: float
    meals_served: int
    partners_earned_paise: int | None  # None under 5 paid partners
    partners: Band


@dataclass(frozen=True)
class SocietyImpact:
    society_id: uuid.UUID
    period: str  # all | YYYY-MM
    as_of: datetime
    homes_joined: int | None  # None under 5 households
    items_donated: int | None  # the next three: None under 5 contributing households
    kg_donated: float | None
    meals_donated: int | None
    contributors: Band
    hub: HubImpact | None  # None when asked without it (the public page) or without a hub


def invalid_period(detail: str) -> ProblemError:
    return ProblemError(422, "request.invalid", detail)


def month_start(day: date) -> date:
    return day.replace(day=1)


def next_month(first: date) -> date:
    return (first.replace(day=28) + timedelta(days=4)).replace(day=1)


def period_month(period: str, now: date | None = None) -> date | None:
    """The first day of an IST month `period`; None for `all`. 422 `request.invalid` for anything
    else, a month in the future, or one older than the last 12 (this one included)."""
    if period == "all":
        return None
    current = month_start(now or today())
    try:
        year, month = (int(part) for part in period.split("-"))
        first = date(year, month, 1)
    except ValueError as exc:
        raise invalid_period("`period` is `all` or a month like 2027-02.") from exc
    ago = (current.year - first.year) * 12 + current.month - first.month
    if ago < 0:
        raise invalid_period("`period` is in the future.")
    if ago >= MONTHS:
        raise invalid_period(f"`period` goes back at most {MONTHS} months.")
    return first


def window(first: date | None) -> tuple[datetime, datetime] | None:
    """[start, end) instants of the IST month starting `first`; None for all time."""
    if first is None:
        return None
    return (
        datetime.combine(first, time.min, IST),
        datetime.combine(next_month(first), time.min, IST),
    )


def band(n: int) -> Band:
    return "5+" if n >= MIN_CONTRIBUTORS else "under_5"


# --- me ---------------------------------------------------------------------------------------


async def my_impact(session: AsyncSession, user_id: uuid.UUID) -> MyImpact:
    """The user's own giving, all time, computed live from M9's tables."""
    donations = (
        (
            await session.execute(
                select(Donation)
                .where(Donation.user_id == user_id, Donation.status.in_(VERIFIED))
                .order_by(Donation.id)
            )
        )
        .scalars()
        .all()
    )
    items = kg_tenths = points = 0
    per_line: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for donation in donations:
        n, kg = counted(donation)
        items, kg_tenths = items + n, kg_tenths + kg
        points += donation.points_awarded or 0
        for line in donation.items:
            per_line[line["code"], line["unit"]] += Decimal(str(line.get("counted") or 0))
    invited, invited_points = (
        await session.execute(
            select(func.count(), func.coalesce(func.sum(Referral.referrer_points), 0)).where(
                Referral.referrer_user_id == user_id, Referral.status == "converted"
            )
        )
    ).one()
    welcome = await session.scalar(
        select(func.coalesce(func.sum(Referral.referee_points), 0)).where(
            Referral.referee_user_id == user_id, Referral.status == "converted"
        )
    )
    meals = await session.scalar(
        select(func.coalesce(func.sum(MealPledge.meals), 0)).where(MealPledge.user_id == user_id)
    )
    return MyImpact(
        items_donated=items,
        kg_donated=kg_tenths / 10,
        donations_verified=len(donations),
        points_earned=points + int(invited_points) + int(welcome or 0),
        meals_donated=int(meals or 0),
        homes_invited=int(invited),
        donated=[
            DonatedLine(code, unit, float(qty))
            for (code, unit), qty in sorted(per_line.items())
            if qty > 0
        ],
    )


# --- a society --------------------------------------------------------------------------------


def iso_label(monday: date) -> str:
    year, week, _ = monday.isocalendar()
    return f"{year}-W{week:02d}"


async def hub_partners_paid(
    session: AsyncSession, hub_id: uuid.UUID, data_source: str, first: date
) -> tuple[int, int]:
    """(partners paid, paise) for the hub's partners over the ISO weeks whose Monday is in the IST
    month starting `first` (a week belongs to the month of its Monday, as in M12 §3.6). Ids and
    sums only, through M5's and M8's facades."""
    last = next_month(first) - timedelta(days=1)
    first_monday = first + timedelta(days=-first.weekday() % 7)
    last_monday = last - timedelta(days=last.weekday())
    earned = await billing_api.earning_facts(
        session, week_from=iso_label(first_monday), week_to=iso_label(last_monday)
    )
    partners = {
        p.partner_id: p
        for p in await partners_api.partner_facts(session, ids={e.partner_id for e in earned})
    }
    totals: dict[uuid.UUID, int] = defaultdict(int)
    for fact in earned:
        partner = partners.get(fact.partner_id)
        if partner is not None and partner.hub_id == hub_id and partner.data_source == data_source:
            totals[fact.partner_id] += fact.amount_paise
    paid = [paise for paise in totals.values() if paise > 0]
    return len(paid), sum(paid)


async def _handed(
    session: AsyncSession,
    hub_id: uuid.UUID,
    kind: str,
    column: InstrumentedAttribute[int],
    span: tuple[datetime, datetime] | None,
) -> int:
    """The sum of `column` over the hub's hand-offs of one kind (in the month, if given)."""
    stmt = select(func.coalesce(func.sum(column), 0)).where(
        NgoHandoff.hub_id == hub_id, NgoHandoff.kind == kind
    )
    if span is not None:
        stmt = stmt.where(NgoHandoff.handed_at >= span[0], NgoHandoff.handed_at < span[1])
    return int(await session.scalar(stmt) or 0)


async def _hub_impact(
    session: AsyncSession, society: Society, first: date | None
) -> HubImpact | None:
    if society.hub_id is None:
        return None
    span = window(first)
    # Partner pay is the month's: the asked one, or this month for `all`.
    paid, paise = await hub_partners_paid(
        session, society.hub_id, society.data_source, first or month_start(today())
    )
    return HubImpact(
        food_saved_kg=await _handed(session, society.hub_id, "food", NgoHandoff.kg_tenths, span)
        / 10,
        meals_served=await _handed(session, society.hub_id, "meals", NgoHandoff.meals, span),
        partners_earned_paise=paise if paid >= MIN_CONTRIBUTORS else None,
        partners=band(paid),
    )


async def society_impact(
    session: AsyncSession,
    society_id: uuid.UUID,
    period: str = "all",
    *,
    with_hub: bool = True,
) -> SocietyImpact | None:
    """The `society` scope of §3.7 with the k rules applied; None for an unknown society. The hub
    block (food, meals served, partner earnings) is read only when `with_hub`."""
    society = await session.get(Society, society_id)
    if society is None:
        return None
    first = period_month(period)
    span = window(first)
    owners = await identity_api.society_owners(session, society_id)
    homes = set(owners.values())

    donations = select(Donation).where(
        Donation.society_id == society_id, Donation.status.in_(VERIFIED)
    )
    referrals = select(Referral.referee_household_id).where(
        Referral.society_id == society_id, Referral.status == "converted"
    )
    pledges = select(MealPledge).where(MealPledge.user_id.in_(owners))
    if span is not None and first is not None:
        donations = donations.where(Donation.verified_at >= span[0], Donation.verified_at < span[1])
        referrals = referrals.where(
            Referral.converted_on >= first, Referral.converted_on < next_month(first)
        )
        pledges = pledges.where(MealPledge.pledged_at >= span[0], MealPledge.pledged_at < span[1])
    given = (await session.execute(donations)).scalars().all()
    pledged = (await session.execute(pledges)).scalars().all()
    contributing = (
        {d.household_id for d in given}
        | set((await session.execute(referrals)).scalars())
        | {owners[p.user_id] for p in pledged}
    )
    shown = len(contributing) >= MIN_CONTRIBUTORS
    items = kg_tenths = 0
    for donation in given:
        n, kg = counted(donation)
        items, kg_tenths = items + n, kg_tenths + kg
    return SocietyImpact(
        society_id=society_id,
        period=period,
        as_of=datetime.now(UTC),
        homes_joined=len(homes) if len(homes) >= MIN_CONTRIBUTORS else None,
        items_donated=items if shown else None,
        kg_donated=kg_tenths / 10 if shown else None,
        meals_donated=sum(p.meals for p in pledged) if shown else None,
        contributors=band(len(contributing)),
        hub=await _hub_impact(session, society, first) if with_hub else None,
    )
