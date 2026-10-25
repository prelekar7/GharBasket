"""`as_of` and `stale` for analytics responses (spec §3.2), for the endpoints that read the views.

`as_of` is the finish time of the last successful run that refreshes the view: any of the three
jobs for the 15-minute views, the nightly run or a rebuild for the others. `stale` is true when
there is none, or it is more than 45 minutes old (15-minute views) or 26 hours (nightly views).
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.analytics.refresh import (
    DAILY_VIEWS,
    NIGHTLY,
    REBUILD,
    REFRESH,
    VIEWS,
    last_ok,
)

STALE_AFTER_15_MINUTES = timedelta(minutes=45)
STALE_AFTER_NIGHTLY = timedelta(hours=26)


@dataclass(frozen=True)
class Freshness:
    as_of: datetime | None
    stale: bool


def jobs_refreshing(view: str) -> tuple[str, ...]:
    if view not in VIEWS:
        raise ValueError(f"not an analytics view: {view!r}")
    return (REFRESH, NIGHTLY, REBUILD) if view in DAILY_VIEWS else (NIGHTLY, REBUILD)


def stale_after(view: str) -> timedelta:
    return STALE_AFTER_15_MINUTES if view in DAILY_VIEWS else STALE_AFTER_NIGHTLY


def judge(view: str, as_of: datetime | None, now: datetime) -> Freshness:
    """The rule on its own: stale without a run, or past the view's limit."""
    return Freshness(as_of, as_of is None or now - as_of > stale_after(view))


async def freshness(session: AsyncSession, view: str, *, now: datetime | None = None) -> Freshness:
    """The `as_of` and `stale` of one materialised view (`mv_daily_hub_kpis`, to)."""
    last = await last_ok(session, jobs_refreshing(view))
    return judge(view, None if last is None else last.finished_at, now or datetime.now(UTC))
