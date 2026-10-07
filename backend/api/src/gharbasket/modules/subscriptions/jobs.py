"""Scheduled jobs of M3 (spec §6), registered with the platform's jobs runner (ADR-0021).

`generate_weekly_plans` runs at T-3 18:00 IST for delivery day T: `scheduled_for` decides the
delivery date (three days later, IST), never the clock. `apply_auto_approve` runs at T-1 20:55
IST, five minutes before M4's cut-off, for the next day's deliveries.
"""

from datetime import timedelta
from typing import Any
from zoneinfo import ZoneInfo

from gharbasket.modules.community import api as community_api
from gharbasket.modules.subscriptions import service
from gharbasket.platform.jobs import JobContext, job

IST = ZoneInfo("Asia/Kolkata")
DAYS_AHEAD = 3


@job(
    "generate_weekly_plans",
    "Draft every Autopilot household's box for the delivery day three days on",
)
async def generate_weekly_plans(ctx: JobContext) -> dict[str, Any]:
    delivery_date = ctx.scheduled_for.astimezone(IST).date() + timedelta(days=DAYS_AHEAD)
    totals: dict[str, int] = {"hubs": 0, "created": 0, "existing": 0, "skipped": 0}
    async with service.http_client(ctx.http) as http:
        for hub_id in await community_api.hubs_with_societies(ctx.session):
            counts = await service.generate_for_hub(
                ctx.session, http, ctx.settings, hub_id=hub_id, delivery_date=delivery_date
            )
            totals["hubs"] += 1
            for key, value in counts.items():
                totals[key] += value
    totals["delivery_date"] = delivery_date.isoformat()  # type: ignore[assignment]
    return totals


@job(
    "apply_auto_approve",
    "Approve tomorrow's boxes for households that asked for it (notice sent 24 h before)",
)
async def apply_auto_approve(ctx: JobContext) -> dict[str, Any]:
    delivery_date = ctx.scheduled_for.astimezone(IST).date() + timedelta(days=1)
    counts: dict[str, Any] = await service.auto_approve_due(ctx.session, delivery_date)
    counts["delivery_date"] = delivery_date.isoformat()
    return counts
