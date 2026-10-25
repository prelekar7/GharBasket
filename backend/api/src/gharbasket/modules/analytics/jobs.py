"""Scheduled jobs of M12 Console & Analytics (spec §3.2, §6): the analytics refresh.

- `refresh_analytics` every 15 minutes (cron-job.org): delivery dates today - 3 to today + 1 (IST)
  and community facts since its last run, then `mv_daily_hub_kpis` and `mv_impact_monthly`.
- `refresh_analytics_nightly` at 02:40 IST (GitHub Actions): today - 35 to today, every household,
  the last 6 ISO weeks of partner earnings and the assignment runs, then every view.
- `rebuild_analytics` by hand: every date since the first order, in 7-day chunks, then every view.

It consumes `identity.user_deleted` (spec §4): the purged household's household and cycle facts go.
"""

import uuid
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.analytics import refresh
from gharbasket.modules.identity import api as identity_api
from gharbasket.platform.jobs import JobContext, job
from gharbasket.platform.models import OutboxEvent
from gharbasket.platform.outbox import subscribe

log = structlog.get_logger("gharbasket.analytics")


@job(
    "refresh_analytics",
    "Every 15 minutes: analytics facts for today - 3 ... today + 1 (IST), then the daily views",
)
async def refresh_analytics(ctx: JobContext) -> dict[str, Any]:
    return await refresh.run(ctx.session, refresh.REFRESH, ctx.scheduled_for)


@job(
    "refresh_analytics_nightly",
    "02:40 IST: analytics facts for the last 35 days, households and partner weeks; every view",
)
async def refresh_analytics_nightly(ctx: JobContext) -> dict[str, Any]:
    return await refresh.run(ctx.session, refresh.NIGHTLY, ctx.scheduled_for)


@job("rebuild_analytics", "By hand: every analytics fact since the first order, then every view")
async def rebuild_analytics(ctx: JobContext) -> dict[str, Any]:
    return await refresh.run(ctx.session, refresh.REBUILD, ctx.scheduled_for)


async def on_user_deleted(session: AsyncSession, event: OutboxEvent) -> None:
    """Spec §4: M1's purge took the household; its household and cycle facts go too. While a
    refresh runs, `RefreshRunning` leaves the event to the outbox's retry."""
    try:
        household_id = uuid.UUID(str(event.payload["household_id"]))
    except (KeyError, ValueError):
        return  # no household (a partner-only account): M12 holds nothing of the user
    removed = await refresh.forget_household(session, household_id)
    log.info("analytics_household_forgotten", **removed)


subscribe(identity_api.USER_DELETED, on_user_deleted)
