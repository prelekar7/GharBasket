"""Jobs of M6 Hub Operations (spec §6, ADR-0021): the procurement plan at T-1 21:10, its refresh
at T-1 23:05 after the payment grace, a rebuild when M10 stores a new forecast, and the vendor
scores of the past week on Monday at 03:33.

`scheduled_for` decides the delivery date (the next IST day) or the week, never the clock.
"""

import uuid
from collections import Counter
from datetime import date, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.hub_ops import procurement, scores, service
from gharbasket.platform.jobs import JobContext, job
from gharbasket.platform.models import OutboxEvent
from gharbasket.platform.outbox import subscribe


async def _plans(ctx: JobContext, *, existing_only: bool) -> dict[str, Any]:
    day = ctx.scheduled_for.astimezone(procurement.IST).date() + timedelta(days=1)
    hubs = await service.active_hub_ids(ctx.session)
    outcomes: Counter[str] = Counter()
    for hub_id in hubs:
        _, outcome = await procurement.build(ctx.session, hub_id, day, existing_only=existing_only)
        outcomes[outcome] += 1
    return {"delivery_date": day.isoformat(), "hubs": len(hubs), **outcomes}


@job("build_procurement_plan", "Build or rebuild tomorrow's procurement plan at every hub (21:10)")
async def build_procurement_plan(ctx: JobContext) -> dict[str, Any]:
    return await _plans(ctx, existing_only=False)


@job("refresh_procurement_plan", "Rebuild tomorrow's plans after the payment grace (23:05)")
async def refresh_procurement_plan(ctx: JobContext) -> dict[str, Any]:
    return await _plans(ctx, existing_only=True)


@job("compute_vendor_scores", "Score every vendor on the past IST week (Monday 03:33)")
async def compute_vendor_scores(ctx: JobContext) -> dict[str, Any]:
    today = ctx.scheduled_for.astimezone(procurement.IST).date()
    week = today - timedelta(days=today.weekday() + 7)  # the Monday of the week before
    return {"week": week.isoformat(), "vendors": await scores.compute(ctx.session, week)}


async def on_forecast_ready(session: AsyncSession, event: OutboxEvent) -> None:
    """`intel.forecast_ready { hub_id, delivery_dates }`: rebuild those dates' plans that exist
    and are not closed."""
    hub_id = uuid.UUID(event.payload["hub_id"])
    for day in event.payload["delivery_dates"]:
        await procurement.build(session, hub_id, date.fromisoformat(day), existing_only=True)


subscribe("intel.forecast_ready", on_forecast_ready)
