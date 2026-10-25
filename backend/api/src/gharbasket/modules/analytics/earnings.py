"""Partner earnings (spec §3.5): the weekly distribution of what one kind of partner earned,
never an individual. One hub reads `mv_partner_earnings_weekly`; an admin who gives no hub gets
the same numbers over every hub, worked out from the weekly partner facts beside it (one Gini,
not an average of hubs'). Under 5 paid partners only the count is sent: the total, percentiles
and Gini are `null`, since fewer says nothing and one partner's total is their earnings.
"""

import math
import uuid
from collections import defaultdict
from datetime import date, timedelta
from fractions import Fraction

from sqlalchemy import BigInteger, Date, Integer, String, Uuid, column, func, select, table
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.analytics import freshness
from gharbasket.modules.analytics.kpis import MIN_PARTNERS, gini_bp
from gharbasket.modules.analytics.models import FactPartnerWeek
from gharbasket.modules.analytics.reads import monday, read_limit
from gharbasket.modules.analytics.schemas import (
    KpiSource,
    PartnerEarningsOut,
    PartnerEarningsWeekOut,
    PartnerKind,
)

VIEW = table(
    "mv_partner_earnings_weekly",
    column("hub_id", Uuid),
    column("kind", String),
    column("iso_week", Date),
    column("data_source", String),
    column("partners_paid", BigInteger),
    column("total_paise", BigInteger),
    column("p10_paise", BigInteger),
    column("median_paise", BigInteger),
    column("p90_paise", BigInteger),
    column("gini_bp", Integer),
    schema="analytics",
)


def percentile_paise(ascending: list[int], p: Fraction) -> int:
    """Linear interpolation between the closest ranks (Postgres `percentile_cont`), rounded half
    up to a whole paisa."""
    position = p * (len(ascending) - 1)
    low = math.floor(position)
    high = min(low + 1, len(ascending) - 1)
    value = ascending[low] + (position - low) * (ascending[high] - ascending[low])
    return math.floor(value + Fraction(1, 2))


def private(row: PartnerEarningsWeekOut) -> PartnerEarningsWeekOut:
    """Under 5 paid partners only the count leaves M12, for every caller: with one partner the
    total is that person's earnings, and with a few it is close to it."""
    if row.partners >= MIN_PARTNERS:
        return row
    return row.model_copy(
        update={
            "total_paise": None,
            "p10_paise": None,
            "median_paise": None,
            "p90_paise": None,
            "gini_bp": None,
        }
    )


def week_row(week: date, paid: list[int]) -> PartnerEarningsWeekOut:
    """One week's distribution over what each paid partner earned."""
    ranked = sorted(paid)
    enough = len(ranked) >= MIN_PARTNERS
    return private(
        PartnerEarningsWeekOut(
            week=week,
            partners=len(ranked),
            total_paise=sum(ranked),
            p10_paise=percentile_paise(ranked, Fraction(1, 10)) if enough else None,
            median_paise=percentile_paise(ranked, Fraction(1, 2)) if enough else None,
            p90_paise=percentile_paise(ranked, Fraction(9, 10)) if enough else None,
            gini_bp=gini_bp(ranked),
        )
    )


async def _from_view(
    session: AsyncSession,
    hub_id: uuid.UUID,
    kind: PartnerKind,
    source: KpiSource,
    first: date,
    last: date,
) -> dict[date, PartnerEarningsWeekOut]:
    rows = await session.execute(
        select(
            VIEW.c.iso_week,
            VIEW.c.partners_paid,
            VIEW.c.total_paise,
            VIEW.c.p10_paise,
            VIEW.c.median_paise,
            VIEW.c.p90_paise,
            VIEW.c.gini_bp,
        ).where(
            VIEW.c.hub_id == hub_id,
            VIEW.c.kind == kind,
            VIEW.c.data_source == source,
            VIEW.c.iso_week.between(first, last),
        )
    )
    return {
        week: private(
            PartnerEarningsWeekOut(
                week=week,
                partners=int(partners),
                total_paise=int(total),
                p10_paise=p10,
                median_paise=median,
                p90_paise=p90,
                gini_bp=gini,
            )
        )
        for week, partners, total, p10, median, p90, gini in rows.all()
    }


async def _from_facts(
    session: AsyncSession, kind: PartnerKind, source: KpiSource, first: date, last: date
) -> dict[date, PartnerEarningsWeekOut]:
    rows = await session.execute(
        select(
            FactPartnerWeek.iso_week,
            FactPartnerWeek.partner_id,
            func.sum(FactPartnerWeek.amount_paise),
        )
        .where(
            FactPartnerWeek.kind == kind,
            FactPartnerWeek.data_source == source,
            FactPartnerWeek.iso_week.between(first, last),
        )
        .group_by(FactPartnerWeek.iso_week, FactPartnerWeek.partner_id)
    )
    paid = defaultdict[date, list[int]](list)
    for week, _partner, paise in rows.all():
        if paise > 0:
            paid[week].append(int(paise))
    return {week: week_row(week, amounts) for week, amounts in paid.items()}


async def partner_earnings(
    session: AsyncSession,
    hub_id: uuid.UUID | None,
    kind: PartnerKind,
    source: KpiSource,
    start: date,
    end: date,
) -> PartnerEarningsOut:
    """Every ISO week from `start` to `end`; a week nobody was paid in has 0 partners and nothing
    else."""
    first, last = monday(start), monday(end)
    async with read_limit(session):
        found = (
            await _from_view(session, hub_id, kind, source, first, last)
            if hub_id is not None
            else await _from_facts(session, kind, source, first, last)
        )
        fresh = await freshness.freshness(session, "mv_partner_earnings_weekly")
    weeks = [first + timedelta(weeks=i) for i in range((last - first).days // 7 + 1)]
    return PartnerEarningsOut(
        hub_id=hub_id,
        kind=kind,
        data_source=source,
        as_of=fresh.as_of,
        stale=fresh.stale,
        weeks=[found.get(week) or week_row(week, []) for week in weeks],
    )
