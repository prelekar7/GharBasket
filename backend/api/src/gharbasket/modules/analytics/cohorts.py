"""Cohort retention (spec §3.5), read from `mv_cohorts`: for each signup ISO week, the share of
its households with a delivered order in each of the following weeks. An admin's view, per data
source, for one hub or every hub (the households and the active ones are added up across hubs).
"""

import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta

from sqlalchemy import BigInteger, Date, SmallInteger, String, Uuid, column, func, select, table
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.analytics import freshness
from gharbasket.modules.analytics.kpis import BP, ratio
from gharbasket.modules.analytics.reads import IST, monday, read_limit
from gharbasket.modules.analytics.schemas import CohortOut, CohortsOut, KpiSource
from gharbasket.platform.errors import ProblemError

MIN_WEEKS, MAX_WEEKS, DEFAULT_WEEKS = 4, 26, 12  # the view holds week offsets 1-26
SMALL_COHORT = 5  # under this many households a rate says little and the console greys it

COHORTS = table(
    "mv_cohorts",
    column("data_source", String),
    column("hub_id", Uuid),
    column("cohort_week", Date),
    column("week_offset", SmallInteger),
    column("households", BigInteger),
    column("active", BigInteger),
    schema="analytics",
)


def check_weeks(weeks: int) -> None:
    if weeks > MAX_WEEKS:
        raise ProblemError(
            422, "analytics.range_too_long", f"Cohorts cover at most {MAX_WEEKS} weeks."
        )
    if weeks < MIN_WEEKS:
        raise ProblemError(
            422, "analytics.range_invalid", f"Cohorts cover at least {MIN_WEEKS} weeks."
        )


def reached(cohort_week: date, offset: int, today: date) -> bool:
    """Week `offset` after the signup week counts only once it has ended: a week in progress
    would read as lost households."""
    return cohort_week + timedelta(weeks=offset, days=6) < today


def covered_to(today: date, as_of: datetime | None) -> date:
    """The day the view has seen: today, or the IST day of its last refresh when that is earlier.
    The view is nightly, so until a refresh has run after a week's Sunday (or when one failed),
    that week's deliveries are not all in it yet and its rate would read low."""
    return today if as_of is None else min(today, as_of.astimezone(IST).date())


async def cohorts(
    session: AsyncSession, hub_id: uuid.UUID | None, source: KpiSource, weeks: int, today: date
) -> CohortsOut:
    """The last `weeks` signup weeks (this one last) and their retention over the next `weeks`."""
    first = monday(today) - timedelta(weeks=weeks - 1)
    stmt = (
        select(
            COHORTS.c.cohort_week,
            COHORTS.c.week_offset,
            func.sum(COHORTS.c.households),
            func.sum(COHORTS.c.active),
        )
        .where(
            COHORTS.c.data_source == source,
            COHORTS.c.cohort_week >= first,
            COHORTS.c.week_offset <= weeks,
        )
        .group_by(COHORTS.c.cohort_week, COHORTS.c.week_offset)
    )
    if hub_id is not None:
        stmt = stmt.where(COHORTS.c.hub_id == hub_id)
    async with read_limit(session):
        rows = (await session.execute(stmt)).all()
        fresh = await freshness.freshness(session, "mv_cohorts")
    seen = covered_to(today, fresh.as_of)

    sizes: dict[date, int] = {}
    active = defaultdict[date, dict[int, int]](dict)
    for cohort_week, offset, households, now_active in rows:
        sizes[cohort_week] = int(households)  # the same on every offset's row
        active[cohort_week][int(offset)] = int(now_active)
    return CohortsOut(
        data_source=source,
        hub_id=hub_id,
        weeks=weeks,
        as_of=fresh.as_of,
        stale=fresh.stale,
        cohorts=[
            CohortOut(
                week=week,
                households=sizes[week],
                small=sizes[week] < SMALL_COHORT,
                retention_bp=[
                    ratio(active[week].get(offset, 0), sizes[week], BP)
                    if reached(week, offset, seen)
                    else None
                    for offset in range(1, weeks + 1)
                ],
            )
            for week in sorted(sizes)
            if sizes[week] > 0
        ],
    )
