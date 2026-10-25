"""The console's impact report (spec §3.6): a month of `mv_impact_monthly` for the platform, one hub
or one NGO, as JSON or CSV.

- **Scopes.** `platform` is for admins, `hub` for the hub's manager and franchise owner (and
  admins), `ngo` for the NGO's coordinators, admins and the staff of a hub the NGO serves (who see
  only that hub's hand-offs). A hub or platform report is the hub totals (`ngo_id` null rows); an
  NGO report is only what the NGO received (its rows): donors, referrals, societies, pay and waste
  are `null`, so a coordinator never sees a household, a society or a partner's pay.
- **Small numbers.** Households donating are null under 5, and partner pay under 5 paid partners.
- **Months** are IST calendar months, the last 24 (this one included); the trend is the 6 months
  up to the one asked.
- **Where the facts stop.** A donated meal belongs to a hub in the facts, so a society's
  `meals_donated` is null; partner pay by month is the ISO weeks whose Monday falls in it.
"""

import csv
import io
import uuid
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import BigInteger, Date, String, Uuid, column, func, select, table
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.analytics import freshness
from gharbasket.modules.analytics.impact_schemas import (
    ImpactNgoOut,
    ImpactOut,
    ImpactScope,
    ImpactSocietyOut,
    ImpactTotalsOut,
    ImpactTrendOut,
)
from gharbasket.modules.analytics.kpis import GRAMS_PER_100_KG, MIN_PARTNERS, ratio
from gharbasket.modules.analytics.models import FactCommunity, FactHubDay, FactPartnerWeek
from gharbasket.modules.analytics.reads import monday, read_limit
from gharbasket.modules.analytics.schemas import KpiSource
from gharbasket.modules.community import api as community_api
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.permissions import Permit, forbidden

MIN_HOUSEHOLDS = 5  # households donating, shown from here (k = 5, as M9 §3.7)
MONTHS = 24
TREND_MONTHS = 6
MAX_CSV_ROWS = 10_000
STAFF = ("hub_manager", "franchise_owner")  # hub staff, by the hub they hold
COORDINATOR = "ngo_coordinator"

VIEW_SUMS = (
    "donations_verified",
    "items_donated",
    "kg_tenths_donated",
    "households_donating",
    "meals_pledged",
    "meals_served",
    "food_kg_tenths",
    "handoffs",
    "people_reached",
    "referrals_converted",
    "societies_live",
    "handoff_items",
    "handoff_kg_tenths",
)
IMPACT = table(
    "mv_impact_monthly",
    column("hub_id", Uuid),
    column("ngo_id", Uuid),
    column("month", Date),
    column("data_source", String),
    *(column(name, BigInteger) for name in VIEW_SUMS),
    schema="analytics",
)
CSV_COLUMNS = (
    "month",
    "hub_id",
    "ngo_id",
    "food_saved_kg",
    "items_donated",
    "kg_donated",
    "donations_verified",
    "households_donating",
    "meals_donated",
    "meals_served",
    "people_reached",
    "referrals_converted",
    "societies_live",
    "partners_earned_paise",
    "partners_paid",
    "waste_g_per_100kg",
)

Row = Mapping[str, object]


def _int(value: object) -> int:
    return int(value or 0)  # pyright: ignore[reportArgumentType]


# --- who may read what ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    """What a request reads: the scope, the ids to echo, and the hubs to read (None: all)."""

    scope: ImpactScope
    hub_id: uuid.UUID | None
    ngo_id: uuid.UUID | None
    hubs: frozenset[uuid.UUID] | None


def staff_hubs(permit: Permit) -> frozenset[uuid.UUID]:
    return frozenset(
        g.scope_id for g in permit.grants if g.role in STAFF and g.scope_id is not None
    )


def coordinates(permit: Permit, ngo_id: uuid.UUID) -> bool:
    return any(g.role == COORDINATOR and g.scope_id == ngo_id for g in permit.grants)


def _must_name(permit: Permit, what: str) -> ProblemError:
    """403 for a caller who may not read everything, 422 for an admin who left it out."""
    if not permit.is_global:
        return forbidden(f"Give the {what} you want to read.")
    return ProblemError(422, "request.invalid", f"Give the {what} to read.")


async def resolve(
    session: AsyncSession,
    permit: Permit,
    scope: ImpactScope,
    hub_id: uuid.UUID | None,
    ngo_id: uuid.UUID | None,
    source: KpiSource,
) -> Target:
    """The target of a request, or 403 `auth.forbidden` (a hub, NGO, scope or data source outside
    the caller's grants), 404 `community.ngo_not_found` (an admin's unknown NGO)."""
    if source == "synthetic" and not permit.is_global:
        raise forbidden("Synthetic data is for admins.")
    if scope == "platform":
        if not permit.is_global:
            raise forbidden("The platform report is for admins.")
        return Target(scope, None, None, None)
    if scope == "hub":
        if hub_id is None:
            raise _must_name(permit, "hub_id")
        if not permit.is_global and hub_id not in staff_hubs(permit):
            raise forbidden()
        return Target(scope, hub_id, None, frozenset([hub_id]))
    if ngo_id is None:
        raise _must_name(permit, "ngo_id")
    own = permit.is_global or coordinates(permit, ngo_id)
    ngo = (await community_api.ngo_refs(session, [ngo_id])).get(ngo_id)
    if ngo is None:
        if own:
            raise ProblemError(404, "community.ngo_not_found", "No such NGO.")
        raise forbidden()
    if own:
        return Target(scope, hub_id, ngo_id, None if hub_id is None else frozenset([hub_id]))
    # Hub staff see an NGO's hand-offs at the hubs they manage and it serves, and no others.
    reachable = staff_hubs(permit) & set(ngo.hub_ids)
    if hub_id is not None:
        reachable &= {hub_id}
    if not reachable:
        raise forbidden()
    return Target(scope, hub_id, ngo_id, frozenset(reachable))


# --- months -------------------------------------------------------------------------------------


def month_start(day: date) -> date:
    return day.replace(day=1)


def add_months(first: date, n: int) -> date:
    index = first.year * 12 + first.month - 1 + n
    return date(index // 12, index % 12 + 1, 1)


def label(first: date) -> str:
    return f"{first.year:04d}-{first.month:02d}"


def resolve_month(month: str | None, today: date) -> date:
    """The first day of the IST month asked for (default this one); 422 `analytics.range_invalid`
    for a future month and `analytics.range_too_long` for one older than the last 24."""
    current = month_start(today)
    if month is None:
        return current
    year, number = (int(part) for part in month.split("-"))
    first = date(year, number, 1)
    if first > current:
        raise ProblemError(422, "analytics.range_invalid", "That month has not happened yet.")
    if first < add_months(current, -(MONTHS - 1)):
        raise ProblemError(
            422, "analytics.range_too_long", f"The report goes back {MONTHS} months at most."
        )
    return first


# --- the numbers --------------------------------------------------------------------------------


def _sum(rows: Sequence[Row], key: str) -> int:
    return sum(_int(r[key]) for r in rows)


def _people(rows: Sequence[Row]) -> int | None:
    """As NGOs report it: null when none of the rows has a figure."""
    reported = [_int(r["people_reached"]) for r in rows if r["people_reached"] is not None]
    return sum(reported) if reported else None


def donor_totals(rows: Sequence[Row]) -> dict[str, int | float | None]:
    """A hub's (or the platform's) month from its hub rows (`ngo_id` null)."""
    households = _sum(rows, "households_donating")
    return {
        "food_saved_kg": _sum(rows, "food_kg_tenths") / 10,
        "items_donated": _sum(rows, "items_donated"),
        "kg_donated": _sum(rows, "kg_tenths_donated") / 10,
        "donations_verified": _sum(rows, "donations_verified"),
        "households_donating": households if households >= MIN_HOUSEHOLDS else None,
        "meals_donated": _sum(rows, "meals_pledged"),
        "meals_served": _sum(rows, "meals_served"),
        "people_reached": _people(rows),
        "referrals_converted": _sum(rows, "referrals_converted"),
        "societies_live": _sum(rows, "societies_live"),
    }


def received_totals(rows: Sequence[Row]) -> dict[str, int | float | None]:
    """What an NGO received, from its rows: items and kilograms of items hand-offs, the surplus
    food, the meals served and the people reached. Nothing about donors."""
    food = _sum(rows, "food_kg_tenths")
    return {
        "food_saved_kg": food / 10,
        "items_donated": _sum(rows, "handoff_items"),
        "kg_donated": (_sum(rows, "handoff_kg_tenths") - food) / 10,
        "meals_served": _sum(rows, "meals_served"),
        "people_reached": _people(rows),
    }


def totals_of(scope: ImpactScope, rows: Sequence[Row]) -> dict[str, int | float | None]:
    return received_totals(rows) if scope == "ngo" else donor_totals(rows)


@dataclass(frozen=True)
class Pay:
    """Paid partners' earnings: each partner's month, per hub and over the platform."""

    by_hub: Mapping[uuid.UUID, list[int]]
    overall: list[int]


def pay_summary(earnings: Sequence[int]) -> tuple[int | None, int | None]:
    """(paise, partners paid), null under 5 paid partners."""
    if len(earnings) < MIN_PARTNERS:
        return None, None
    return sum(earnings), len(earnings)


async def _pay(
    session: AsyncSession, hubs: frozenset[uuid.UUID] | None, source: KpiSource, first: date
) -> Pay:
    last = add_months(first, 1) - timedelta(days=1)
    first_monday = first + timedelta(days=-first.weekday() % 7)
    where = [
        FactPartnerWeek.data_source == source,
        FactPartnerWeek.iso_week.between(first_monday, monday(last)),
    ]
    if hubs is not None:
        where.append(FactPartnerWeek.hub_id.in_(hubs))
    rows = await session.execute(
        select(
            FactPartnerWeek.hub_id,
            FactPartnerWeek.partner_id,
            func.sum(FactPartnerWeek.amount_paise),
        )
        .where(*where)
        .group_by(FactPartnerWeek.hub_id, FactPartnerWeek.partner_id)
    )
    by_hub: dict[uuid.UUID, list[int]] = defaultdict(list)
    per_partner: dict[uuid.UUID, int] = defaultdict(int)
    for hub_id, partner_id, paise in rows.all():
        if int(paise) > 0:
            by_hub[hub_id].append(int(paise))
        per_partner[partner_id] += int(paise)
    return Pay(by_hub, [paise for paise in per_partner.values() if paise > 0])


async def _waste(
    session: AsyncSession, hubs: frozenset[uuid.UUID] | None, source: KpiSource, first: date
) -> dict[uuid.UUID, tuple[int, int]]:
    """Per hub: (bin grams, packed grams) of the month."""
    where = [
        FactHubDay.data_source == source,
        FactHubDay.on_date >= first,
        FactHubDay.on_date < add_months(first, 1),
    ]
    if hubs is not None:
        where.append(FactHubDay.hub_id.in_(hubs))
    rows = await session.execute(
        select(FactHubDay.hub_id, func.sum(FactHubDay.waste_bin_g), func.sum(FactHubDay.packed_g))
        .where(*where)
        .group_by(FactHubDay.hub_id)
    )
    return {hub_id: (int(bin_g), int(packed_g)) for hub_id, bin_g, packed_g in rows.all()}


async def _view_rows(
    session: AsyncSession, target: Target, source: KpiSource, first: date, last: date
) -> list[Row]:
    stmt = select(IMPACT).where(IMPACT.c.data_source == source, IMPACT.c.month.between(first, last))
    if target.scope == "ngo":
        stmt = stmt.where(IMPACT.c.ngo_id == target.ngo_id)
    if target.hubs is not None:
        stmt = stmt.where(IMPACT.c.hub_id.in_(target.hubs))
    return [dict(row) for row in (await session.execute(stmt)).mappings().all()]


async def _societies(
    session: AsyncSession, hub_id: uuid.UUID, source: KpiSource, first: date
) -> list[ImpactSocietyOut]:
    """The hub's societies with the month's verified donations and the households joined."""
    given = {
        society_id: (int(items), int(kg_tenths))
        for society_id, items, kg_tenths in (
            await session.execute(
                select(
                    FactCommunity.society_id,
                    func.sum(FactCommunity.items),
                    func.sum(FactCommunity.kg_tenths),
                )
                .where(
                    FactCommunity.hub_id == hub_id,
                    FactCommunity.kind == "donation_verified",
                    FactCommunity.data_source == source,
                    FactCommunity.on_date >= first,
                    FactCommunity.on_date < add_months(first, 1),
                )
                .group_by(FactCommunity.society_id)
            )
        ).all()
    }
    return [
        ImpactSocietyOut(
            society_id=society.id,
            name=society.name,
            items_donated=given.get(society.id, (0, 0))[0],
            kg_donated=given.get(society.id, (0, 0))[1] / 10,
            meals_donated=None,
            households_joined=society.joined,
        )
        for society in await community_api.hub_societies(session, hub_id=hub_id)
        if society.data_source == source
    ]


def _ngo_rows(rows: Sequence[Row], names: Mapping[uuid.UUID, str]) -> list[ImpactNgoOut]:
    by_ngo: dict[uuid.UUID, list[Row]] = defaultdict(list)
    for row in rows:
        if isinstance(row["ngo_id"], uuid.UUID):
            by_ngo[row["ngo_id"]].append(row)
    return sorted(
        (
            ImpactNgoOut(
                ngo_id=ngo_id,
                name=names.get(ngo_id, "NGO"),
                handoffs=_sum(mine, "handoffs"),
                items=_sum(mine, "handoff_items"),
                kg=_sum(mine, "handoff_kg_tenths") / 10,
                meals=_sum(mine, "meals_served"),
                people_reached=_people(mine),
            )
            for ngo_id, mine in by_ngo.items()
        ),
        key=lambda n: (n.name, str(n.ngo_id)),
    )


@dataclass(frozen=True)
class Report:
    """A month, read once: the view rows of the trend window and the extras of the month."""

    target: Target
    source: KpiSource
    first: date
    rows: list[Row]
    pay: Pay
    waste: dict[uuid.UUID, tuple[int, int]]
    names: Mapping[uuid.UUID, str]

    def in_month(self) -> list[Row]:
        return [r for r in self.rows if r["month"] == self.first]


async def load(
    session: AsyncSession, target: Target, source: KpiSource, first: date
) -> tuple[Report, freshness.Freshness]:
    async with read_limit(session):
        rows = await _view_rows(session, target, source, add_months(first, 1 - TREND_MONTHS), first)
        names = {
            ngo_id: ref.name
            for ngo_id, ref in (
                await community_api.ngo_refs(
                    session, {r["ngo_id"] for r in rows if isinstance(r["ngo_id"], uuid.UUID)}
                )
            ).items()
        }
        wide = target.scope != "ngo"
        pay = await _pay(session, target.hubs, source, first) if wide else Pay({}, [])
        waste = await _waste(session, target.hubs, source, first) if wide else {}
        fresh = await freshness.freshness(session, "mv_impact_monthly")
    return Report(target, source, first, rows, pay, waste, names), fresh


def _waste_rate(waste: Mapping[uuid.UUID, tuple[int, int]], hubs: set[uuid.UUID]) -> int | None:
    return ratio(
        sum(waste[h][0] for h in hubs if h in waste),
        sum(waste[h][1] for h in hubs if h in waste),
        GRAMS_PER_100_KG,
    )


def build(
    report: Report, societies: list[ImpactSocietyOut] | None, fresh: freshness.Freshness
) -> ImpactOut:
    target, scope = report.target, report.target.scope
    hub_rows = [r for r in report.in_month() if scope == "ngo" or r["ngo_id"] is None]
    values = totals_of(scope, hub_rows)
    if scope == "ngo":
        totals = ImpactTotalsOut.model_validate(
            {
                "donations_verified": None,
                "households_donating": None,
                "meals_donated": None,
                "referrals_converted": None,
                "societies_live": None,
                "partners_earned_paise": None,
                "partners_paid": None,
                "waste_g_per_100kg": None,
                **values,
            }
        )
    else:
        paise, paid = pay_summary(report.pay.overall)
        totals = ImpactTotalsOut.model_validate(
            {
                **values,
                "partners_earned_paise": paise,
                "partners_paid": paid,
                "waste_g_per_100kg": _waste_rate(report.waste, set(report.waste)),
            }
        )
    months = [add_months(report.first, -i) for i in range(TREND_MONTHS - 1, -1, -1)]
    per_month = [
        totals_of(
            scope,
            [r for r in report.rows if r["month"] == m and (scope == "ngo" or r["ngo_id"] is None)],
        )
        for m in months
    ]
    return ImpactOut(
        scope=scope,
        hub_id=target.hub_id,
        ngo_id=target.ngo_id,
        month=label(report.first),
        data_source=report.source,
        as_of=fresh.as_of,
        stale=fresh.stale,
        totals=totals,
        trend=ImpactTrendOut(
            months=[label(m) for m in months],
            food_saved_kg=[float(v["food_saved_kg"] or 0) for v in per_month],
            items_donated=[int(v["items_donated"] or 0) for v in per_month],
            meals_served=[int(v["meals_served"] or 0) for v in per_month],
        ),
        societies=societies,
        ngos=_ngo_rows(report.in_month(), report.names),
    )


async def report(
    session: AsyncSession, target: Target, source: KpiSource, first: date
) -> ImpactOut:
    """The JSON report of one month."""
    loaded, fresh = await load(session, target, source, first)
    societies = None
    if target.scope == "hub" and target.hub_id is not None:
        async with read_limit(session):
            societies = await _societies(session, target.hub_id, source, first)
    return build(loaded, societies, fresh)


# --- CSV ------------------------------------------------------------------------------------------


def csv_rows(loaded: Report) -> list[list[object]]:
    """One row per (month, hub, ngo) of the month: the hub's totals (ngo blank), then each NGO's
    receipts at that hub. An NGO report has only its own rows, and no pay, waste or donors."""
    target = loaded.target
    month = label(loaded.first)
    in_month = loaded.in_month()
    hubs = sorted({r["hub_id"] for r in in_month}, key=lambda h: (h is None, str(h)))
    out: list[list[object]] = []

    def line(hub: object, ngo: object, values: Mapping[str, object]) -> list[object]:
        return [month, hub or "", ngo or "", *(values.get(c) for c in CSV_COLUMNS[3:])]

    for hub in hubs:
        mine = [r for r in in_month if r["hub_id"] == hub]
        if target.scope != "ngo":
            hub_values: dict[str, object] = dict(
                donor_totals([r for r in mine if r["ngo_id"] is None])
            )
            earned = loaded.pay.by_hub.get(hub, []) if isinstance(hub, uuid.UUID) else []
            hub_values["partners_earned_paise"], hub_values["partners_paid"] = pay_summary(earned)
            hub_values["waste_g_per_100kg"] = (
                _waste_rate(loaded.waste, {hub}) if isinstance(hub, uuid.UUID) else None
            )
            out.append(line(hub, None, hub_values))
        for row in sorted(
            (r for r in mine if r["ngo_id"] is not None), key=lambda r: str(r["ngo_id"])
        ):
            out.append(line(hub, row["ngo_id"], received_totals([row])))
    return out[:MAX_CSV_ROWS]


def render_csv(loaded: Report) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_COLUMNS)
    writer.writerows(csv_rows(loaded))
    return buffer.getvalue()


def filename(target: Target, first: date) -> str:
    who = target.ngo_id or target.hub_id
    return f"impact-{label(first)}-{target.scope}{'' if who is None else f'-{str(who)[:8]}'}.csv"
