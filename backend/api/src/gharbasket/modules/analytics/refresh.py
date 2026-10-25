"""The analytics refresh (spec §3.1, §3.2): facts pulled through the source modules' facades into
M12's own `analytics.fact_*` tables, then the materialised views over them refreshed.

- **Windows** are IST dates from the run's `scheduled_for`: `refresh_analytics` pulls delivery dates
  today - 3 to today + 1 and community facts since its last successful run; the nightly run pulls
  today - 35 to today, every household, the partner weeks of the last 6 ISO weeks and the assignment
  runs; a rebuild pulls every date from the first order (or signup, if earlier), in 7-day chunks.
- **Upserts** go by source id and change a row (and its `synced_at`) only when a value changed,
  so a rerun changes nothing. A fact the source no longer returns for a pulled window (a purged
  cycle, a superseded route) is removed: the source wins.
- **Runs** are recorded in `analytics.refresh_runs` with the rows read, changed and removed per
  fact. A failed pull leaves the previous facts in place (its writes are rolled back to a
  savepoint), the run is committed as `failed`, and the error is raised so the job run fails too.
- The three jobs never interleave: each takes one shared advisory lock for the whole run.
- **Erasure** (spec §4): `forget_household`, the consumer of `identity.user_deleted`, deletes a
  purged household's facts under the same lock, so no refresh in flight writes them back.
"""

import uuid
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, Table, and_, delete, func, select, text, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.analytics.models import (
    FactAssignmentRun,
    FactCommunity,
    FactCycle,
    FactHousehold,
    FactHubDay,
    FactIssue,
    FactOrder,
    FactPartnerWeek,
    FactRoute,
    RefreshRun,
)
from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.community import api as community_api
from gharbasket.modules.hub_ops import api as hub_ops_api
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.logistics import api as logistics_api
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.partners import api as partners_api
from gharbasket.modules.subscriptions import api as subs_api
from gharbasket.platform.jobs import lock_key

IST = ZoneInfo("Asia/Kolkata")
REFRESH = "refresh_analytics"
NIGHTLY = "refresh_analytics_nightly"
REBUILD = "rebuild_analytics"
DAILY_VIEWS = ("mv_daily_hub_kpis", "mv_impact_monthly")
VIEWS = (
    "mv_daily_hub_kpis",
    "mv_weekly_unit_economics",
    "mv_cohorts",
    "mv_partner_earnings_weekly",
    "mv_impact_monthly",
)
BEFORE, AFTER = 3, 1  # refresh_analytics: today - 3 to today + 1
NIGHTLY_DAYS = 35  # 48-hour issue credits, redeliveries and the 14-day carry-over refunds
PARTNER_WEEKS = 6
CHUNK_DAYS = 7
BATCH = 1_000  # rows per INSERT: well under asyncpg's 32,767 parameters
ERROR_LENGTH = 500
# The community kinds M9's facades give today; the others (hand-offs, pledges) come with M9 §3.5.
PULLED_KINDS = ("donation_verified", "referral_converted", "society_live")


class RefreshFailed(RuntimeError):
    """A pull or a view refresh failed: the run is recorded `failed`, the facts are unchanged."""


# --- windows (IST) --------------------------------------------------------------------------------


def ist_today(at: datetime) -> date:
    return at.astimezone(IST).date()


def monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def refresh_window(today: date) -> tuple[date, date]:
    """`refresh_analytics`'s delivery dates: today - 3 to today + 1."""
    return today - timedelta(days=BEFORE), today + timedelta(days=AFTER)


def nightly_window(today: date) -> tuple[date, date]:
    """The nightly run's dates: today - 35 to today."""
    return today - timedelta(days=NIGHTLY_DAYS), today


def partner_weeks(today: date) -> tuple[date, date]:
    """The Mondays of the last 6 ISO weeks, this one included."""
    return monday(today) - timedelta(weeks=PARTNER_WEEKS - 1), monday(today)


def chunks(date_from: date, date_to: date, days: int = CHUNK_DAYS) -> list[tuple[date, date]]:
    """[date_from, date_to] cut into consecutive inclusive spans of at most `days` days."""
    spans: list[tuple[date, date]] = []
    start = date_from
    while start <= date_to:
        end = min(start + timedelta(days=days - 1), date_to)
        spans.append((start, end))
        start = end + timedelta(days=1)
    return spans


def iso_week(monday_: date) -> str:
    year, number, _ = monday_.isocalendar()
    return f"{year}-W{number:02d}"


# --- writing facts ----------------------------------------------------------------------------


@dataclass
class Counts:
    read: dict[str, int] = field(default_factory=dict[str, int])
    changed: dict[str, int] = field(default_factory=dict[str, int])
    removed: dict[str, int] = field(default_factory=dict[str, int])

    def add(self, fact: str, read: int, changed: int, removed: int = 0) -> None:
        self.read[fact] = self.read.get(fact, 0) + read
        self.changed[fact] = self.changed.get(fact, 0) + changed
        self.removed[fact] = self.removed.get(fact, 0) + removed

    def as_dict(self) -> dict[str, Any]:
        return {"read": self.read, "changed": self.changed, "removed": self.removed}


Model = (
    type[FactHousehold]
    | type[FactCycle]
    | type[FactOrder]
    | type[FactIssue]
    | type[FactRoute]
    | type[FactHubDay]
    | type[FactPartnerWeek]
    | type[FactAssignmentRun]
    | type[FactCommunity]
)


def _table(model: Model) -> Table:
    return cast(Table, model.__table__)


def _batches[T](rows: Sequence[T], size: int = BATCH) -> Iterable[Sequence[T]]:
    for start in range(0, len(rows), size):
        yield rows[start : start + size]


async def upsert(
    session: AsyncSession, model: Model, rows: Sequence[dict[str, Any]], now: datetime
) -> int:
    """Insert new facts and update changed ones by primary key; an unchanged row is not
    touched, so its `synced_at` stays. Returns the rows inserted or updated."""
    table = _table(model)
    keys = [column.name for column in table.primary_key.columns]
    values = [c.name for c in table.columns if c.name not in keys and c.name != "synced_at"]
    changed = 0
    for batch in _batches(rows):
        stmt = insert(table).values([{**row, "synced_at": now} for row in batch])
        stmt = stmt.on_conflict_do_update(
            index_elements=keys,
            set_={**{name: stmt.excluded[name] for name in values}, "synced_at": now},
            where=tuple_(*(table.c[name] for name in values)).is_distinct_from(
                tuple_(*(stmt.excluded[name] for name in values))
            ),
        )
        changed += len((await session.execute(stmt.returning(table.c[keys[0]]))).all())
    return changed


async def prune(
    session: AsyncSession,
    model: Model,
    keep: Iterable[tuple[Any, ...]],
    within: ColumnElement[bool] | None,
) -> int:
    """Delete the facts `within` the pulled scope (all of them when None) whose key the source
    no longer returned. Returns the rows deleted."""
    table = _table(model)
    pk = list(table.primary_key.columns)
    stmt = select(*pk)
    if within is not None:
        stmt = stmt.where(within)
    kept = set(keep)
    gone = [tuple(row) for row in (await session.execute(stmt)).all() if tuple(row) not in kept]
    for batch in _batches(gone):
        await session.execute(delete(table).where(tuple_(*pk).in_(batch)))
    return len(gone)


async def _write(
    session: AsyncSession,
    counts: Counts,
    fact: str,
    model: Model,
    rows: list[dict[str, Any]],
    now: datetime,
    within: ColumnElement[bool] | None = None,
    *,
    pruned: bool = True,
) -> None:
    """Upsert `rows`, then (when `pruned`) remove the facts `within` the pulled scope (every
    one when None) that `rows` no longer has."""
    changed = await upsert(session, model, rows, now)
    removed = 0
    if pruned:
        keys = [column.name for column in _table(model).primary_key.columns]
        removed = await prune(session, model, (tuple(r[k] for k in keys) for r in rows), within)
    counts.add(fact, len(rows), changed, removed)


def _between(column: Any, date_from: date, date_to: date) -> ColumnElement[bool]:
    return and_(column >= date_from, column <= date_to)


# --- the pulls ------------------------------------------------------------------------------------


async def pull_dated(
    session: AsyncSession, counts: Counts, date_from: date, date_to: date, now: datetime
) -> None:
    """Cycles, orders, issues, hub-days and routes of the delivery dates [date_from, date_to]."""
    cycles = await subs_api.cycle_facts(session, date_from=date_from, date_to=date_to)
    await _write(
        session,
        counts,
        "cycles",
        FactCycle,
        [
            {
                "cycle_id": c.cycle_id,
                "household_id": c.household_id,
                "hub_id": c.hub_id,
                "society_id": c.society_id,
                "week_start": c.week_start,
                "delivery_date": c.delivery_date,
                "status": c.status,
                "auto_approved": c.auto_approved,
                "approved_before_cutoff": c.approved_before_cutoff,
                "edits": c.edits,
                "plan_source": c.plan_source,
                "ran_out_taps": c.ran_out_taps,
                "wasted_taps": c.wasted_taps,
                "just_right_taps": c.just_right_taps,
                "data_source": c.data_source,
            }
            for c in cycles
        ],
        now,
        _between(FactCycle.delivery_date, date_from, date_to),
    )
    orders = await orders_api.order_facts(session, date_from=date_from, date_to=date_to)
    await _write(
        session,
        counts,
        "orders",
        FactOrder,
        [
            {
                "order_id": o.order_id,
                "household_id": o.household_id,
                "hub_id": o.hub_id,
                "society_id": o.society_id,
                "kind": o.kind,
                "delivery_date": o.delivery_date,
                "status": o.status,
                "total_paise": o.total_paise,
                "fees_paise": o.fees_paise,
                "refunded_paise": o.refunded_paise,
                "credited_paise": o.credited_paise,
                "lines": o.lines,
                "short_lines": o.short_lines,
                "on_time": on_time(o),
                "first_attempt": first_attempt(o),
                "data_source": o.data_source,
            }
            for o in orders
        ],
        now,
        _between(FactOrder.delivery_date, date_from, date_to),
    )
    issues = await orders_api.issue_facts(session, date_from=date_from, date_to=date_to)
    # No prune: M4 never deletes an issue, and an issue's fact has no delivery date to scope by.
    await _write(
        session,
        counts,
        "issues",
        FactIssue,
        [
            {
                "issue_id": i.issue_id,
                "order_id": i.order_id,
                "hub_id": i.hub_id,
                "kind": i.kind,
                "status": i.status,
                "amount_paise": i.amount_paise,
                "credit_paise": i.credit_paise,
                "resolved_by": i.resolved_by,
                "created_at": i.created_at,
                "resolved_at": i.resolved_at,
                "data_source": i.data_source,
            }
            for i in issues
        ],
        now,
        pruned=False,
    )
    days = await hub_ops_api.hub_day_facts(session, date_from=date_from, date_to=date_to)
    await _write(
        session,
        counts,
        "hub_days",
        FactHubDay,
        [
            {
                "hub_id": d.hub_id,
                "date": d.on,
                "packed_g": d.packed_g,
                "waste_ngo_g": d.waste_ngo_g,
                "waste_bin_g": d.waste_bin_g,
                "waste_kitchen_g": d.waste_kitchen_g,
                "waste_imperfect_g": d.waste_imperfect_g,
                "cogs_paise": d.cogs_paise,
                "surplus_offered_g": d.surplus_offered_g,
                "surplus_taken_g": d.surplus_taken_g,
                "data_source": d.data_source,
            }
            for d in days
        ],
        now,
        _between(FactHubDay.on_date, date_from, date_to),
    )
    routes = await logistics_api.route_facts(session, date_from=date_from, date_to=date_to)
    await _write(
        session,
        counts,
        "routes",
        FactRoute,
        [
            {
                "route_id": r.route_id,
                "hub_id": r.hub_id,
                "date": r.on,
                "kind": r.kind,
                "km_m": r.km_m,
                "cost_paise": r.cost_paise,
                "orders": r.orders,
                "stops": r.stops,
                "on_time_stops": r.on_time_stops,
                "data_source": r.data_source,
            }
            for r in routes
        ],
        now,
        _between(FactRoute.on_date, date_from, date_to),
    )


def on_time(order: orders_api.OrderFact) -> bool | None:
    """Delivered by the end of its slot; None until delivered."""
    if order.status != "delivered" or order.delivered_at is None:
        return None
    return order.delivered_at <= order.slot_end


def first_attempt(order: orders_api.OrderFact) -> bool | None:
    """Delivered on its first trip out; None until it was dispatched."""
    if order.attempts == 0:
        return None
    return order.status == "delivered" and order.attempts == 1


async def pull_community(
    session: AsyncSession, counts: Counts, date_from: date, date_to: date, now: datetime
) -> None:
    """Verified donations, converted referrals and societies gone live on [date_from, date_to]."""
    impact = await community_api.impact_facts(session, date_from=date_from, date_to=date_to)
    referrals = await community_api.referral_facts(
        session, date_from=date_from, date_to=date_to, by="converted"
    )
    rows = [
        {
            "source": f.source,
            "source_id": f.source_id,
            "hub_id": f.hub_id,
            "society_id": f.society_id,
            "ngo_id": f.ngo_id,
            "date": f.on,
            "kind": f.kind,
            "items": f.items,
            "kg_tenths": f.kg_tenths,
            "meals": f.meals,
            "points": f.points,
            "people_reached": f.people_reached,
            "household_id": f.household_id,
            "data_source": f.data_source,
        }
        for f in impact
    ] + [
        {
            "source": "referral",
            "source_id": r.referral_id,
            "hub_id": r.hub_id,
            "society_id": r.society_id,
            "ngo_id": None,
            "date": r.converted_on,
            "kind": "referral_converted",
            "items": 0,
            "kg_tenths": 0,
            "meals": 0,
            "points": (r.referrer_points or 0) + (r.referee_points or 0),
            "people_reached": None,
            "household_id": None,
            "data_source": r.data_source,
        }
        for r in referrals
        if r.converted_on is not None
    ]
    await _write(
        session,
        counts,
        "community",
        FactCommunity,
        rows,
        now,
        and_(
            _between(FactCommunity.on_date, date_from, date_to),
            FactCommunity.kind.in_(PULLED_KINDS),
        ),
    )


async def pull_households(session: AsyncSession, counts: Counts, now: datetime) -> list[date]:
    """Every household (a purged one leaves); returns their signup dates."""
    households = await identity_api.household_facts(session)
    await _write(
        session,
        counts,
        "households",
        FactHousehold,
        [
            {
                "household_id": h.household_id,
                "hub_id": h.hub_id,
                "society_id": h.society_id,
                "created_on": h.created_on,
                "cohort_week": monday(h.created_on),
                "status": h.status,
                "data_source": h.data_source,
            }
            for h in households
        ],
        now,
        None,
    )
    return [h.created_on for h in households]


async def pull_partner_weeks(
    session: AsyncSession, counts: Counts, first: date, last: date, now: datetime
) -> None:
    """Earnings per partner, ISO week and basis for the weeks whose Mondays are first to last,
    tagged with the partner's hub, kind and data source. An earning of a partner M8 does not know
    is left out (counted as `unmatched`)."""
    earnings = await billing_api.earning_facts(
        session, week_from=iso_week(first), week_to=iso_week(last)
    )
    partners = {
        p.partner_id: p
        for p in await partners_api.partner_facts(session, ids={e.partner_id for e in earnings})
    }
    rows = [
        {
            "partner_id": e.partner_id,
            "iso_week": date.fromisocalendar(int(e.iso_week[:4]), int(e.iso_week[6:]), 1),
            "basis": e.basis,
            "hub_id": partners[e.partner_id].hub_id,
            "kind": partners[e.partner_id].kind,
            "units": e.units,
            "amount_paise": e.amount_paise,
            "data_source": partners[e.partner_id].data_source,
        }
        for e in earnings
        if e.partner_id in partners
    ]
    await _write(
        session,
        counts,
        "partner_weeks",
        FactPartnerWeek,
        rows,
        now,
        _between(FactPartnerWeek.iso_week, first, last),
    )
    counts.add("partner_weeks_unmatched", len(earnings) - len(rows), 0)


async def pull_assignment_runs(
    session: AsyncSession, counts: Counts, date_from: date, date_to: date, now: datetime
) -> None:
    runs = await partners_api.assignment_run_facts(session, date_from=date_from, date_to=date_to)
    await _write(
        session,
        counts,
        "assignment_runs",
        FactAssignmentRun,
        [
            {
                "run_id": r.run_id,
                "hub_id": r.hub_id,
                "date": r.on,
                "meal": r.meal,
                "portions": r.portions,
                "gini_bp": r.gini_bp,
                "km_per_portion_m": r.km_per_portion_m,
                "data_source": r.data_source,
            }
            for r in runs
        ],
        now,
        _between(FactAssignmentRun.on_date, date_from, date_to),
    )


async def refresh_views(session: AsyncSession, views: Sequence[str]) -> None:
    for name in views:
        # The names are this module's constants, never input.
        await session.execute(text(f"REFRESH MATERIALIZED VIEW CONCURRENTLY analytics.{name}"))


# --- the three jobs ---------------------------------------------------------------------------


async def last_ok(session: AsyncSession, jobs: Sequence[str]) -> RefreshRun | None:
    """The newest successful run of any of `jobs`."""
    return (
        await session.execute(
            select(RefreshRun)
            .where(RefreshRun.job.in_(jobs), RefreshRun.status == "ok")
            .order_by(RefreshRun.finished_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


@dataclass(frozen=True)
class Plan:
    window_from: date
    window_to: date
    work: Callable[[AsyncSession, Counts, datetime], Awaitable[None]]
    views: Sequence[str]


async def _plan(session: AsyncSession, job: str, today: date) -> Plan:
    if job == REFRESH:
        date_from, date_to = refresh_window(today)
        # Community facts since the last successful 15-minute run (never further back than the
        # nightly window, which the nightly run covers anyway).
        last = await last_ok(session, (REFRESH,))
        since = date_from if last is None else ist_today(last.scheduled_for)
        community_from = max(min(since, date_from), nightly_window(today)[0])

        async def refresh(session: AsyncSession, counts: Counts, now: datetime) -> None:
            await pull_dated(session, counts, date_from, date_to, now)
            await pull_community(session, counts, community_from, date_to, now)

        return Plan(community_from, date_to, refresh, DAILY_VIEWS)
    if job == NIGHTLY:
        date_from, date_to = nightly_window(today)
        first_week, last_week = partner_weeks(today)

        async def nightly(session: AsyncSession, counts: Counts, now: datetime) -> None:
            await pull_households(session, counts, now)
            await pull_dated(session, counts, date_from, date_to, now)
            await pull_community(session, counts, date_from, date_to, now)
            await pull_partner_weeks(session, counts, first_week, last_week, now)
            await pull_assignment_runs(session, counts, date_from, date_to, now)

        return Plan(min(date_from, first_week), date_to, nightly, VIEWS)
    if job == REBUILD:
        # From the first order (or the first signup, if earlier: the cohorts start there).
        signups = [h.created_on for h in await identity_api.household_facts(session)]
        first_order = await orders_api.first_delivery_date(session)
        starts: list[date] = [*signups, today] + ([first_order] if first_order else [])
        first = min(starts)
        last = today + timedelta(days=AFTER)

        async def rebuild(session: AsyncSession, counts: Counts, now: datetime) -> None:
            await pull_households(session, counts, now)
            for span_from, span_to in chunks(first, last):
                await pull_dated(session, counts, span_from, span_to, now)
                await pull_community(session, counts, span_from, span_to, now)
                await pull_assignment_runs(session, counts, span_from, span_to, now)
            await pull_partner_weeks(session, counts, monday(first), monday(last), now)

        return Plan(monday(first), last, rebuild, VIEWS)
    raise ValueError(f"not an analytics job: {job!r}")


LOCK = "analytics_facts"  # the one advisory lock of the three jobs and the erasure below


class RefreshRunning(RuntimeError):
    """A refresh holds the facts: the erasure is retried later (the outbox's backoff), so a pull
    read before the purge cannot write the household's facts back after it."""


async def forget_household(session: AsyncSession, household_id: uuid.UUID) -> dict[str, int]:
    """Spec §4: after M1's purge, the household's `fact_households` and `fact_cycles` rows go
    (M3 deletes its cycles at the purge); its orders, issues and community facts stay, as M4 and
    M9 keep them. Raises `RefreshRunning` rather than wait for a refresh in progress."""
    taken = await session.scalar(select(func.pg_try_advisory_xact_lock(lock_key(LOCK))))
    if not taken:
        raise RefreshRunning("an analytics refresh is running")
    removed: dict[str, int] = {}
    for name, model, column in (
        ("households", FactHousehold, FactHousehold.household_id),
        ("cycles", FactCycle, FactCycle.household_id),
    ):
        gone = await session.execute(delete(model).where(column == household_id).returning(column))
        removed[name] = len(gone.all())
    return removed


async def run(
    session: AsyncSession, job: str, scheduled_for: datetime, *, now: datetime | None = None
) -> dict[str, Any]:
    """One refresh job, inside the job's transaction. `now` is the facts' `synced_at` (the clock
    by default)."""
    await session.execute(select(func.pg_advisory_xact_lock(lock_key(LOCK))))
    now = now or datetime.now(UTC)
    plan = await _plan(session, job, ist_today(scheduled_for))
    record = RefreshRun(
        job=job,
        scheduled_for=scheduled_for,
        window_from=plan.window_from,
        window_to=plan.window_to,
        status="running",
        started_at=datetime.now(UTC),
    )
    session.add(record)
    await session.flush()
    counts = Counts()
    try:
        async with session.begin_nested():  # a failed pull leaves the previous facts
            await plan.work(session, counts, now)
            await refresh_views(session, plan.views)
    except Exception as exc:
        record.status, record.finished_at = "failed", datetime.now(UTC)
        record.error = f"{type(exc).__name__}: {exc}"[:ERROR_LENGTH]
        record.rows = counts.as_dict()
        await session.commit()  # the failed run is kept although the job's transaction fails
        raise RefreshFailed(f"{job} failed: {type(exc).__name__}") from exc
    record.status, record.finished_at, record.rows = "ok", datetime.now(UTC), counts.as_dict()
    await session.flush()
    return {
        "window": [plan.window_from.isoformat(), plan.window_to.isoformat()],
        **counts.as_dict(),
        "views": list(plan.views),
    }
