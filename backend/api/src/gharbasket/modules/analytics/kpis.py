"""KPIs (spec §3.3): `mv_daily_hub_kpis` summed into periods, with the rates recomputed from their
parts over each period and over the whole range (never averaged). Two things the daily view
cannot give are read from the fact tables beside it: the distinct households with a delivered
order in a period (a daily count cannot be summed into a week), and partner earnings (weekly).

Every key of the table is always sent. A key whose source does not exist yet is `null` in every
period (`UNSOURCED`); one that is undefined for a period (a rate with nothing to divide by) is
`null` there. Counts of nothing are 0.
"""

import math
import uuid
from collections import defaultdict
from collections.abc import Mapping
from datetime import date, timedelta
from fractions import Fraction

from sqlalchemy import BigInteger, Date, String, Uuid, column, func, select, table
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.analytics import freshness
from gharbasket.modules.analytics.models import FactOrder, FactPartnerWeek
from gharbasket.modules.analytics.reads import monday, read_limit
from gharbasket.modules.analytics.schemas import Grain, KpiSource, KpisOut
from gharbasket.platform.errors import ProblemError

BP = 10_000
MAX_DAYS, MAX_WEEKS = 92, 104
DEFAULT_WEEKS = 12
MIN_PARTNERS = 5  # a Gini under this many paid partners says nothing and could identify them

KPI_KEYS = (
    "active_households",
    "weeks_accepted_bp",
    "auto_approved_bp",
    "skipped_bp",
    "accepted_ml_bp",
    "accepted_fallback_bp",
    "edits_per_100_boxes",
    "pantry_misses_bp",
    "orders_delivered",
    "boxes_delivered",
    "gmv_paise",
    "aov_paise",
    "on_time_bp",
    "first_attempt_bp",
    "shortage_lines_bp",
    "waste_g_per_100kg",
    "delivery_cost_per_order_paise",
    "km_per_order_m",
    "issues_per_1000_orders",
    "issue_auto_credit_bp",
    "partner_earnings_paise",
    "partners_paid",
    "kitchen_gini_bp",
    "donations_verified",
    "meals_donated",
    "referrals_converted",
    "societies_live",
)
# Keys whose facts do not exist yet: null until they do.
# - waste_g_per_100kg: the hub-day facts still hold 0 in every waste column (the refresh's As
#   built, M12 §3.2), and a 0 there would read as "no waste". The facade must read waste logs first.
# - meals_donated: M9 §3.5's meal pledges and hand-offs have no facade yet, so no fact row exists.
UNSOURCED = ("waste_g_per_100kg", "meals_donated")
# Keys read from the weekly partner facts: a day cannot be cut out of a week.
WEEKLY_ONLY = ("partner_earnings_paise", "partners_paid", "kitchen_gini_bp")

SUMS = (
    "orders_delivered",
    "boxes_delivered",
    "gmv_paise",
    "on_time_orders",
    "first_attempt_orders",
    "dispatched_orders",
    "short_lines",
    "packed_lines",
    "cycles_generated",
    "cycles_approved",
    "cycles_auto_approved",
    "cycles_skipped",
    "cycles_ml",
    "cycles_ml_approved",
    "cycles_fallback",
    "cycles_fallback_approved",
    "line_edits",
    "ran_out_taps",
    "wasted_taps",
    "pantry_taps",
    "issues",
    "issues_auto_credited",
    "box_route_km_m",
    "box_route_cost_paise",
    "box_route_orders",
    "donations_verified",
    "referrals_converted",
    "societies_live",
)
DAILY = table(
    "mv_daily_hub_kpis",
    column("hub_id", Uuid),
    column("date", Date),
    column("data_source", String),
    *(column(name, BigInteger) for name in SUMS),
    schema="analytics",
)


def ratio(num: int, den: int, scale: int = 1) -> int | None:
    """`num * scale / den` rounded half up; None without a denominator."""
    return None if den <= 0 else (2 * num * scale + den) // (2 * den)


def gini_bp(earnings: list[int]) -> int | None:
    """Gini of paid partners' earnings in basis points, as `mv_partner_earnings_weekly` computes
    it (2 Σ i·x_i ÷ (n Σ x) - (n + 1) ÷ n over the ascending values); None under 5 partners."""
    n, total = len(earnings), sum(earnings)
    if n < MIN_PARTNERS or total <= 0:
        return None
    ranked = sorted(earnings)
    gini = Fraction(2 * sum(i * x for i, x in enumerate(ranked, 1)), n * total) - Fraction(n + 1, n)
    return math.floor(gini * BP + Fraction(1, 2))


def metrics(s: Mapping[str, int]) -> dict[str, int | None]:
    """The view-backed KPIs from the summed parts of one period (or of the whole range)."""
    delivered = s["orders_delivered"]
    return {
        "weeks_accepted_bp": ratio(s["cycles_approved"], s["cycles_generated"], BP),
        "auto_approved_bp": ratio(s["cycles_auto_approved"], s["cycles_generated"], BP),
        "skipped_bp": ratio(s["cycles_skipped"], s["cycles_generated"], BP),
        "accepted_ml_bp": ratio(s["cycles_ml_approved"], s["cycles_ml"], BP),
        "accepted_fallback_bp": ratio(s["cycles_fallback_approved"], s["cycles_fallback"], BP),
        "edits_per_100_boxes": ratio(s["line_edits"], s["cycles_approved"], 100),
        "pantry_misses_bp": ratio(s["ran_out_taps"] + s["wasted_taps"], s["pantry_taps"], BP),
        "orders_delivered": delivered,
        "boxes_delivered": s["boxes_delivered"],
        "gmv_paise": s["gmv_paise"],
        "aov_paise": ratio(s["gmv_paise"], delivered),
        "on_time_bp": ratio(s["on_time_orders"], delivered, BP),
        "first_attempt_bp": ratio(s["first_attempt_orders"], s["dispatched_orders"], BP),
        "shortage_lines_bp": ratio(s["short_lines"], s["packed_lines"], BP),
        # A route cost of 0 over orders means no cost was recorded (a plan without one), not free.
        "delivery_cost_per_order_paise": (
            ratio(s["box_route_cost_paise"], s["box_route_orders"])
            if s["box_route_cost_paise"] > 0
            else None
        ),
        "km_per_order_m": ratio(s["box_route_km_m"], s["box_route_orders"]),
        "issues_per_1000_orders": ratio(s["issues"], delivered, 1_000),
        "issue_auto_credit_bp": ratio(s["issues_auto_credited"], s["issues"], BP),
        "donations_verified": s["donations_verified"],
        "referrals_converted": s["referrals_converted"],
        "societies_live": s["societies_live"],
    }


def resolve_range(
    from_: date | None, to: date | None, grain: Grain, today: date
) -> tuple[date, date]:
    """The IST dates asked for, defaulting to the last 12 weeks. A week grain covers whole ISO
    weeks: `from` moves back to its Monday and `to` forward to its Sunday."""
    end = to or today
    if grain == "week":
        start = from_ or monday(end) - timedelta(weeks=DEFAULT_WEEKS - 1)
    else:
        start = from_ or end - timedelta(days=DEFAULT_WEEKS * 7 - 1)
    if end < start:
        raise ProblemError(422, "analytics.range_invalid", "`to` is before `from`.")
    if grain == "week":
        start, end = monday(start), monday(end) + timedelta(days=6)
        if (end - start).days + 1 > MAX_WEEKS * 7:
            raise ProblemError(
                422, "analytics.range_too_long", f"A week series covers at most {MAX_WEEKS} weeks."
            )
    elif (end - start).days + 1 > MAX_DAYS:
        raise ProblemError(
            422, "analytics.range_too_long", f"A day series covers at most {MAX_DAYS} days."
        )
    return start, end


def periods_of(start: date, end: date, grain: Grain) -> list[date]:
    step = 7 if grain == "week" else 1
    return [start + timedelta(days=i) for i in range(0, (end - start).days + 1, step)]


async def _daily_sums(
    session: AsyncSession,
    hub_id: uuid.UUID | None,
    source: KpiSource,
    start: date,
    end: date,
    grain: Grain,
) -> dict[date, dict[str, int]]:
    period = DAILY.c.date if grain == "day" else func.date_trunc("week", DAILY.c.date).cast(Date)
    stmt = (
        select(period.label("period"), *(func.sum(DAILY.c[name]).label(name) for name in SUMS))
        .where(DAILY.c.data_source == source, DAILY.c.date.between(start, end))
        .group_by(period)
    )
    if hub_id is not None:
        stmt = stmt.where(DAILY.c.hub_id == hub_id)
    rows = (await session.execute(stmt)).mappings().all()
    return {row["period"]: {name: int(row[name] or 0) for name in SUMS} for row in rows}


async def _active_households(
    session: AsyncSession,
    hub_id: uuid.UUID | None,
    source: KpiSource,
    start: date,
    end: date,
    grain: Grain,
) -> tuple[dict[date, int], int]:
    """Distinct households with a delivered order, per period, and in the last 7 days of the
    range (the "last week" of `totals`)."""
    where = [FactOrder.status == "delivered", FactOrder.data_source == source]
    if hub_id is not None:
        where.append(FactOrder.hub_id == hub_id)
    period = (
        FactOrder.delivery_date
        if grain == "day"
        else func.date_trunc("week", FactOrder.delivery_date).cast(Date)
    )
    per_period = await session.execute(
        select(period, func.count(FactOrder.household_id.distinct()))
        .where(*where, FactOrder.delivery_date.between(start, end))
        .group_by(period)
    )
    last = await session.scalar(
        select(func.count(FactOrder.household_id.distinct())).where(
            *where, FactOrder.delivery_date.between(max(start, end - timedelta(days=6)), end)
        )
    )
    return {day: int(n) for day, n in per_period.all()}, int(last or 0)


async def _partner_keys(
    session: AsyncSession,
    hub_id: uuid.UUID | None,
    source: KpiSource,
    start: date,
    end: date,
) -> tuple[dict[date, dict[str, int | None]], dict[str, int | None]]:
    """Earnings, partners paid and the kitchens' Gini per ISO week, and over the range (each
    partner's earnings added up first, so a partner paid in two weeks counts once)."""
    where = [
        FactPartnerWeek.data_source == source,
        FactPartnerWeek.iso_week.between(monday(start), monday(end)),
    ]
    if hub_id is not None:
        where.append(FactPartnerWeek.hub_id == hub_id)
    rows = await session.execute(
        select(
            FactPartnerWeek.iso_week,
            FactPartnerWeek.partner_id,
            FactPartnerWeek.kind,
            func.sum(FactPartnerWeek.amount_paise),
        )
        .where(*where)
        .group_by(FactPartnerWeek.iso_week, FactPartnerWeek.partner_id, FactPartnerWeek.kind)
    )
    by_week: dict[date, dict[tuple[uuid.UUID, str], int]] = defaultdict(dict)
    overall: dict[tuple[uuid.UUID, str], int] = defaultdict(int)
    for week, partner_id, kind, paise in rows.all():
        by_week[week][partner_id, kind] = int(paise)
        overall[partner_id, kind] += int(paise)

    def summary(earned: Mapping[tuple[uuid.UUID, str], int]) -> dict[str, int | None]:
        paid = {key: paise for key, paise in earned.items() if paise > 0}
        kitchen = [paise for (_, kind), paise in paid.items() if kind == "kitchen"]
        return {
            "partner_earnings_paise": sum(earned.values()),
            "partners_paid": len({partner_id for partner_id, _ in paid}),
            "kitchen_gini_bp": gini_bp(kitchen),
        }

    return {week: summary(earned) for week, earned in by_week.items()}, summary(overall)


async def kpis(
    session: AsyncSession,
    hub_id: uuid.UUID | None,
    source: KpiSource,
    start: date,
    end: date,
    grain: Grain,
) -> KpisOut:
    periods = periods_of(start, end, grain)
    async with read_limit(session):
        sums = await _daily_sums(session, hub_id, source, start, end, grain)
        active, active_last_week = await _active_households(
            session, hub_id, source, start, end, grain
        )
        weekly, weekly_total = (
            await _partner_keys(session, hub_id, source, start, end)
            if grain == "week"
            else ({}, {})
        )
        fresh = await freshness.freshness(session, "mv_daily_hub_kpis")

    zero: dict[str, int] = dict.fromkeys(SUMS, 0)
    per_period: list[dict[str, int | None]] = []
    for period in periods:
        row = metrics(sums.get(period, zero))
        row["active_households"] = active.get(period, 0)
        row.update(weekly.get(period, {"partner_earnings_paise": 0, "partners_paid": 0}))
        per_period.append(row)
    total = metrics({name: sum(s[name] for s in sums.values()) for name in SUMS})
    total["active_households"] = active_last_week
    total.update(weekly_total)

    def blank(key: str) -> bool:
        return key in UNSOURCED or (grain == "day" and key in WEEKLY_ONLY)

    return KpisOut(
        hub_id=hub_id,
        data_source=source,
        grain=grain,
        as_of=fresh.as_of,
        stale=fresh.stale,
        periods=periods,
        series={
            key: [None if blank(key) else row.get(key) for row in per_period] for key in KPI_KEYS
        },
        totals={key: None if blank(key) else total.get(key) for key in KPI_KEYS},
    )
