"""Unit economics and the monthly P&L (spec §3.4), over `mv_weekly_unit_economics`.

Handbook 02 §5: contribution per box = AOV * margin + delivery fee - (delivery + packing + captain
+ payment fee + CAC). Each input is *measured* when the facts give it and an editable *assumption*
otherwise; a what-if override beats both. Everything is whole numbers: paise for money, basis
points for rates, and each percentage step is rounded half up to a whole paisa, the same as the
console's `boxEconomics` (`frontend/console/lib/unit-economics.js`).

The view has one row per hub, ISO week and data source, so a range is whole ISO weeks (as the KPIs'
week grain is) and a month is the weeks that start in it. A week is measured on its own: one week
with no captain earnings yet does not turn the others' into assumptions.
"""

import calendar
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

from sqlalchemy import BigInteger, Date, String, Uuid, column, select, table
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.analytics import assumptions as saved_assumptions
from gharbasket.modules.analytics import freshness
from gharbasket.modules.analytics.economics_schemas import (
    AssumptionsOut,
    BasisShare,
    BasisTimes,
    MeasuredOut,
    PartnersPaidOut,
    PerBoxOut,
    PnlLineOut,
    PnlOut,
    PnlWeekOut,
    SourcesOut,
    UnitEconomicsOut,
    UnitWeekOut,
)
from gharbasket.modules.analytics.kpis import BP, ratio, resolve_range
from gharbasket.modules.analytics.models import ASSUMPTION_KEYS
from gharbasket.modules.analytics.reads import monday, read_limit
from gharbasket.modules.analytics.schemas import KpiSource
from gharbasket.platform.errors import ProblemError

VIEW = "mv_weekly_unit_economics"
MAX_MONTHS = 24  # "any of the last 24 months", this one included
Prefer = Literal["measured", "assumed"]
NUMBERS = (
    "boxes",
    "box_revenue_paise",
    "box_fees_paise",
    "addon_revenue_paise",
    "tiffins",
    "tiffin_revenue_paise",
    "box_route_orders",
    "captain_paise",
    "packer_paise",
    "rider_paise",
    "kitchen_paise",
)
WEEKLY = table(
    VIEW,
    column("hub_id", Uuid),
    column("iso_week", Date),
    column("data_source", String),
    *(column(name, BigInteger) for name in NUMBERS),
    column("cogs_paise", BigInteger),
    column("box_route_cost_paise", BigInteger),
    schema="analytics",
)


@dataclass(frozen=True)
class Week:
    """One ISO week of one hub, as the view has it. `cogs_paise` and `box_route_cost_paise` are
    None when not every day or route of the week has one (a plan without a cost is not free)."""

    boxes: int = 0
    box_revenue_paise: int = 0  # box orders, net of the delivery fee, refunds and credits
    box_fees_paise: int = 0  # the pre-live delivery fee of delivered boxes
    addon_revenue_paise: int = 0
    tiffins: int = 0
    tiffin_revenue_paise: int = 0
    cogs_paise: int | None = None
    box_route_cost_paise: int | None = 0
    box_route_orders: int = 0
    captain_paise: int = 0
    packer_paise: int = 0
    rider_paise: int = 0
    kitchen_paise: int = 0

    @property
    def food_revenue_paise(self) -> int:
        """Boxes and add-ons, net: what the produce share and the AOV are taken of."""
        return self.box_revenue_paise + self.addon_revenue_paise

    @property
    def active(self) -> bool:
        return self.boxes > 0 or self.tiffins > 0 or self.kitchen_paise > 0


# A week the view has no row for: nothing happened, so nothing is missing.
NOTHING = Week(cogs_paise=0)


def combine(weeks: Iterable[Week]) -> Week:
    """Several weeks as one; a cost missing in any of them is missing in the total."""
    listed = list(weeks)
    totals = {name: sum(getattr(w, name) for w in listed) for name in NUMBERS}
    cogs = [w.cogs_paise for w in listed]
    cost = [w.box_route_cost_paise for w in listed]
    return Week(
        **totals,
        cogs_paise=None if None in cogs or not cogs else sum(c or 0 for c in cogs),
        box_route_cost_paise=None if None in cost else sum(c or 0 for c in cost),
    )


async def weeks_of(
    session: AsyncSession, hub_id: uuid.UUID, source: KpiSource, first: date, last: date
) -> dict[date, Week]:
    """The view's weeks whose Monday is in `first ... last`; a week without a row is left out."""
    rows = await session.execute(
        select(WEEKLY).where(
            WEEKLY.c.hub_id == hub_id,
            WEEKLY.c.data_source == source,
            WEEKLY.c.iso_week.between(first, last),
        )
    )
    weeks: dict[date, Week] = {}
    for row in rows.mappings():
        values = {name: int(row[name] or 0) for name in NUMBERS}
        weeks[row["iso_week"]] = Week(
            **values,
            cogs_paise=None if row["cogs_paise"] is None else int(row["cogs_paise"]),
            box_route_cost_paise=(
                None if row["box_route_cost_paise"] is None else int(row["box_route_cost_paise"])
            ),
        )
    return weeks


# --- what is measured --------------------------------------------------------------------------


def measured(w: Week) -> dict[str, int | None]:
    """The inputs the facts give (spec §3.4), None where they cannot yet.

    - `aov_paise`: box and add-on revenue net of refunds and credits ÷ boxes; the fee is left out.
    - `margin_bp`: 1 - COGS ÷ that revenue, once every day of the weeks has a COGS (M6 §3.4).
    - `delivery_paise`: box routes' cost ÷ the orders on them, once their cost is recorded.
    - `packing_paise`, `captain_paise`: the packers' and captains' earnings ÷ boxes delivered
      (boxes packed are not a fact), once any are recorded.
    The payment fee and the CAC are never measured in v1.
    """
    revenue = w.food_revenue_paise
    return {
        "aov_paise": ratio(revenue, w.boxes),
        "margin_bp": (
            ratio(revenue - w.cogs_paise, revenue, BP)
            if w.cogs_paise is not None and revenue > 0
            else None
        ),
        "delivery_paise": (
            ratio(w.box_route_cost_paise, w.box_route_orders)
            if w.box_route_cost_paise and w.box_route_orders > 0
            else None
        ),
        "packing_paise": ratio(w.packer_paise, w.boxes) if w.packer_paise > 0 else None,
        "captain_paise": ratio(w.captain_paise, w.boxes) if w.captain_paise > 0 else None,
    }


def delivery_fee(w: Week) -> int:
    """Σ fees of delivered boxes ÷ boxes: 0 with no pre-live boxes (and with no boxes at all)."""
    return ratio(w.box_fees_paise, w.boxes) or 0


@dataclass(frozen=True)
class Inputs:
    used: dict[str, int]
    sources: dict[str, str]
    delivery_fee_paise: int


def choose(
    saved: Mapping[str, int],
    meas: Mapping[str, int | None],
    fee_measured: int,
    *,
    prefer: Prefer,
    overrides: Mapping[str, int | None],
) -> Inputs:
    """Override > measured (`prefer=measured`) > saved, per key, and which one it was."""
    used: dict[str, int] = {}
    sources: dict[str, str] = {}
    for key in ASSUMPTION_KEYS:
        over, found = overrides.get(key), meas.get(key)
        if over is not None:
            used[key], sources[key] = over, "override"
        elif prefer == "measured" and found is not None:
            used[key], sources[key] = found, "measured"
        else:
            used[key], sources[key] = saved[key], "assumed"
    fee_over = overrides.get("delivery_fee_paise")
    sources["delivery_fee_paise"] = "measured" if fee_over is None else "override"
    return Inputs(used, sources, fee_measured if fee_over is None else fee_over)


def per_box(used: Mapping[str, int], fee_paise: int) -> PerBoxOut:
    """The console's `boxEconomics`, key for key."""
    margin = ratio(used["aov_paise"] * used["margin_bp"], BP) or 0
    payment_fee = ratio(used["aov_paise"] * used["payment_fee_bp"], BP) or 0
    return PerBoxOut(
        margin_paise=margin,
        delivery_fee_paise=fee_paise,
        delivery_paise=used["delivery_paise"],
        packing_paise=used["packing_paise"],
        captain_paise=used["captain_paise"],
        fee_paise=payment_fee,
        cac_paise=used["cac_paise"],
        contribution_paise=margin
        + fee_paise
        - used["delivery_paise"]
        - used["packing_paise"]
        - used["captain_paise"]
        - payment_fee
        - used["cac_paise"],
    )


async def unit_economics(
    session: AsyncSession,
    hub_id: uuid.UUID,
    source: KpiSource,
    from_: date | None,
    to: date | None,
    *,
    prefer: Prefer,
    overrides: Mapping[str, int | None],
    today: date,
) -> UnitEconomicsOut:
    start, end = resolve_range(from_, to, "week", today)
    async with read_limit(session):
        saved = await saved_assumptions.saved(session, hub_id)
        weeks = await weeks_of(session, hub_id, source, start, end)
        fresh = await freshness.freshness(session, VIEW)

    def work(w: Week) -> Inputs:
        return choose(saved, measured(w), delivery_fee(w), prefer=prefer, overrides=overrides)

    total = combine(weeks.values())
    meas = measured(total)
    inputs = work(total)
    series: list[UnitWeekOut] = []
    for i in range((end - start).days // 7 + 1):
        day = start + timedelta(weeks=i)
        w = weeks.get(day, NOTHING)
        one = work(w)
        series.append(
            UnitWeekOut(
                week=day,
                boxes=w.boxes,
                contribution_paise=(
                    per_box(one.used, one.delivery_fee_paise).contribution_paise
                    if w.boxes > 0
                    else None
                ),
            )
        )
    return UnitEconomicsOut.model_validate(
        {
            "hub_id": hub_id,
            "data_source": source,
            "from": start,
            "to": end,
            "as_of": fresh.as_of,
            "boxes": total.boxes,
            "assumptions": AssumptionsOut(**saved),
            "measured": MeasuredOut(**{key: meas[key] for key in MeasuredOut.model_fields}),
            "used": AssumptionsOut(**inputs.used),
            "sources": SourcesOut(**inputs.sources),  # pyright: ignore[reportArgumentType]
            "delivery_fee_paise": inputs.delivery_fee_paise,
            "per_box": per_box(inputs.used, inputs.delivery_fee_paise),
            "weeks": series,
        }
    )


# --- the P&L -----------------------------------------------------------------------------------

LINE_KEYS = (
    "box_revenue",
    "delivery_fee_revenue",
    "tiffin_revenue",
    "produce",
    "kitchen",
    "delivery",
    "packing",
    "captain",
    "fees",
    "acquisition",
)
# The unit a `times` line counts, and the units of a `share` line's quantity.
UNITS = {"tiffin_revenue": "tiffin", "kitchen": "tiffin"}


@dataclass
class Line:
    """A P&L line over one or more weeks: the exact amount, how many units it counts and whether
    every week's amount was measured."""

    amount: int = 0
    qty: int = 0
    measured: bool = True


@dataclass
class Statement:
    lines: dict[str, Line] = field(default_factory=lambda: {key: Line() for key in LINE_KEYS})

    @property
    def revenue(self) -> int:
        return sum(self.lines[k].amount for k in LINE_KEYS[:3])

    @property
    def contribution(self) -> int:
        return sum(line.amount for line in self.lines.values())


def statement_of(w: Week, a: Mapping[str, int]) -> Statement:
    """One week's lines: measured totals where the facts have them, else the saved assumption.
    Revenues are positive, costs negative, all exact paise."""
    s = Statement()
    food = w.food_revenue_paise
    boxes, tiffins = w.boxes, w.tiffins

    def put(key: str, amount: int, qty: int, is_measured: bool) -> None:
        s.lines[key] = Line(amount, qty, is_measured)

    # Add-on revenue belongs to the box line: "box and add-on revenue" (spec §3.4).
    put("box_revenue", food, boxes, True)
    put("delivery_fee_revenue", w.box_fees_paise, boxes, True)
    put("tiffin_revenue", w.tiffin_revenue_paise, tiffins, True)
    if w.cogs_paise is not None:
        put("produce", -w.cogs_paise, boxes, True)
    else:
        put("produce", -(ratio(food * (BP - a["margin_bp"]), BP) or 0), boxes, False)
    # No assumption stands for the tiffin kitchens' pay: unmeasured, it is 0.
    put("kitchen", -w.kitchen_paise, tiffins, w.kitchen_paise > 0 or tiffins == 0)
    if w.box_route_cost_paise:
        put("delivery", -w.box_route_cost_paise, boxes, True)
    else:
        put("delivery", -boxes * a["delivery_paise"], boxes, False)
    if w.packer_paise > 0:
        put("packing", -w.packer_paise, boxes, True)
    else:
        put("packing", -boxes * a["packing_paise"], boxes, False)
    if w.captain_paise > 0:
        put("captain", -w.captain_paise, boxes, True)
    else:
        put("captain", -boxes * a["captain_paise"], boxes, False)
    # The payment fee is a share of the box revenue, as the per-box fee is of the AOV.
    put("fees", -(ratio(food * a["payment_fee_bp"], BP) or 0), boxes, False)
    put("acquisition", -boxes * a["cac_paise"], boxes, False)
    return s


def merge(statements: Iterable[tuple[Week, Statement]]) -> Statement:
    """The month's lines: the weeks' amounts added. A line is measured only if every week that had
    something to measure was (an empty week says nothing)."""
    out = Statement()
    for w, s in statements:
        for key in LINE_KEYS:
            line, into = s.lines[key], out.lines[key]
            into.amount += line.amount
            into.qty += line.qty
            if w.active and not line.measured:
                into.measured = False
    return out


def line_out(key: str, line: Line, a: Mapping[str, int], food: int) -> PnlLineOut:
    if key in ("produce", "fees"):
        # A share of the food revenue: the assumption's own bp when assumed, else what it came to.
        if key == "fees":
            bp = a["payment_fee_bp"]
        elif not line.measured:
            bp = BP - a["margin_bp"]
        else:
            bp = ratio(-line.amount, food, BP) or 0
        basis: BasisTimes | BasisShare = BasisShare(bp=bp)
    else:
        basis = BasisTimes(
            qty=line.qty,
            unit=UNITS.get(key, "box"),  # pyright: ignore[reportArgumentType]
            rate_paise=ratio(abs(line.amount), line.qty) or 0,
        )
    return PnlLineOut(
        key=key,  # pyright: ignore[reportArgumentType]
        amount_paise=line.amount,
        source="measured" if line.measured else "assumed",
        basis=basis,
    )


def parse_period(period: str, today: date) -> tuple[date, date]:
    """`YYYY-MM` as its first and last day: any of the last 24 months, this one included."""
    try:
        year, month = int(period[:4]), int(period[5:7])
        if len(period) != 7 or period[4] != "-" or not (1 <= month <= 12):
            raise ValueError
        first = date(year, month, 1)
    except ValueError:
        raise ProblemError(
            422, "analytics.range_invalid", "The period is a month like 2027-02."
        ) from None
    this_month = today.replace(day=1)
    if first > this_month:
        raise ProblemError(422, "analytics.range_invalid", "That month has not begun.")
    if (this_month.year - year) * 12 + this_month.month - month >= MAX_MONTHS:
        raise ProblemError(
            422, "analytics.range_too_long", f"The P&L covers the last {MAX_MONTHS} months."
        )
    return first, date(year, month, calendar.monthrange(year, month)[1])


def mondays_in(first: date, last: date) -> list[date]:
    start = monday(first)
    start = start if start >= first else start + timedelta(weeks=1)
    return [start + timedelta(weeks=i) for i in range((last - start).days // 7 + 1)]


def month_statement(
    mondays: list[date], weeks: Mapping[date, Week], a: Mapping[str, int]
) -> tuple[Statement, Week, list[tuple[date, Statement]]]:
    pairs = [(day, weeks.get(day, NOTHING)) for day in mondays]
    statements = [(w, statement_of(w, a)) for _, w in pairs]
    total = combine(w for _, w in pairs)
    return (
        merge(statements),
        total,
        [(day, s) for (day, _), (_, s) in zip(pairs, statements, strict=True)],
    )


def previous_month(first: date) -> tuple[date, date]:
    last = first - timedelta(days=1)
    return last.replace(day=1), last


async def pnl(
    session: AsyncSession,
    hub_id: uuid.UUID,
    source: KpiSource,
    period: str,
    today: date,
) -> PnlOut:
    first, last = parse_period(period, today)
    before_first, before_last = previous_month(first)
    async with read_limit(session):
        saved = await saved_assumptions.saved(session, hub_id)
        weeks = await weeks_of(session, hub_id, source, monday(before_first), last)
        fresh = await freshness.freshness(session, VIEW)

    statement, total, per_week = month_statement(mondays_in(first, last), weeks, saved)
    prev_mondays = mondays_in(before_first, before_last)
    prev_statement, _, _ = month_statement(prev_mondays, weeks, saved)
    food = total.food_revenue_paise
    boxes = total.boxes
    had_data = any(weeks.get(day, NOTHING).active for day in prev_mondays)
    return PnlOut(
        hub_id=hub_id,
        period=period,
        as_of=fresh.as_of,
        boxes=boxes,
        tiffins=total.tiffins,
        revenue_paise=statement.revenue,
        contribution_paise=statement.contribution,
        previous_contribution_paise=prev_statement.contribution if had_data else None,
        per_box_paise=ratio(statement.contribution, boxes),
        partners_paise=PartnersPaidOut(
            captains=total.captain_paise,
            kitchens=total.kitchen_paise,
            riders=total.rider_paise,
            packers=total.packer_paise,
        ),
        lines=[line_out(key, statement.lines[key], saved, food) for key in LINE_KEYS],
        weeks=[PnlWeekOut(week=day, contribution_paise=s.contribution) for day, s in per_week],
    )
