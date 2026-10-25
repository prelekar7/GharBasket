"""Scheduled jobs of M12 Console & Analytics (spec §3.2, §6): the analytics refresh.

- `refresh_analytics` every 15 minutes (cron-job.org): delivery dates today - 3 to today + 1 (IST)
  and community facts since its last run, then `mv_daily_hub_kpis` and `mv_impact_monthly`.
- `refresh_analytics_nightly` at 02:40 IST (GitHub Actions): today - 35 to today, every household,
  the last 6 ISO weeks of partner earnings and the assignment runs, then every view.
- `rebuild_analytics` by hand: every date since the first order, in 7-day chunks, then every view.
"""

from typing import Any

from gharbasket.modules.analytics import refresh
from gharbasket.platform.jobs import JobContext, job


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
