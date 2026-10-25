"""Jobs of M6 Hub Operations (spec §6, ADR-0021): the procurement plan at T-1 21:10, its refresh
at T-1 23:05 after the payment grace, a rebuild when M10 stores a new forecast, the vendor scores of
the past week on Monday at 03:33, `build_packing_tasks` at T 05:00 IST and `surplus_cascade` (the
12:30 run, §3.5) at T 12:30 IST.

`scheduled_for` decides the delivery date (its IST calendar day for packing, the next IST day for
procurement) or the week, never the clock.
"""

import uuid
from collections import Counter
from datetime import date, timedelta
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.hub_ops import kept, packing, procurement, scores, service, surplus
from gharbasket.modules.hub_ops.models import KeptItem
from gharbasket.modules.orders import api as orders_api
from gharbasket.platform.jobs import JobContext, job
from gharbasket.platform.models import OutboxEvent
from gharbasket.platform.outbox import subscribe

log = structlog.get_logger("gharbasket.hub_ops")


@job("build_packing_tasks", "Build the day's packing tasks at every active hub (T 05:00 IST)")
async def build_packing_tasks(ctx: JobContext) -> dict[str, Any]:
    day = ctx.scheduled_for.astimezone(packing.IST).date()
    hubs = await service.active_hub_ids(ctx.session)
    created = existing = 0
    for hub_id in hubs:
        made, had = await packing.build(ctx.session, hub_id, day)
        created, existing = created + made, existing + had
    return {
        "delivery_date": day.isoformat(),
        "hubs": len(hubs),
        "created": created,
        "existing": existing,
    }


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


@job(
    "surplus_cascade",
    "The 12:30 run at every active hub: draw packing down, close the day's buying, cascade "
    "surplus to kitchens, NGOs, bin (T 12:30 IST)",
)
async def surplus_cascade(ctx: JobContext) -> dict[str, Any]:
    """Each hub's run in its own savepoint at `scheduled_for`: one hub's failure is logged and
    listed as `failed_hubs`, and the other hubs' runs stand."""
    totals: Counter[str] = Counter(drawn_tasks=0, closed_purchase_orders=0, offers=0, binned=0)
    failed: list[str] = []
    hubs = await service.active_hub_ids(ctx.session)
    for hub_id in hubs:
        try:
            async with ctx.session.begin_nested():
                done = await surplus.run(ctx.session, hub_id, ctx.scheduled_for)
        except Exception as exc:
            log.error("surplus_cascade_failed", hub_id=str(hub_id), error=type(exc).__name__)
            failed.append(str(hub_id))
            continue
        totals.update(done)
    day = ctx.scheduled_for.astimezone(surplus.IST).date()
    return {"date": day.isoformat(), "hubs": len(hubs), **totals} | (
        {"failed_hubs": failed} if failed else {}
    )


async def on_forecast_ready(session: AsyncSession, event: OutboxEvent) -> None:
    """`intel.forecast_ready { hub_id, delivery_dates }`: rebuild those dates' plans that exist
    and are not closed."""
    hub_id = uuid.UUID(event.payload["hub_id"])
    for day in event.payload["delivery_dates"]:
        await procurement.build(session, hub_id, date.fromisoformat(day), existing_only=True)


async def on_carry_over_expired(session: AsyncSession, event: OutboxEvent) -> None:
    """`orders.carry_over_expired { lines: [ { carry_id, … } ] }` (M4's 14 days, M4 §3.8): those
    carry rows were refunded, so their items still `kept` go into returned lots for the next
    12:30 run (§3.5). Items packed or lost already are left alone, so a replay changes nothing."""
    carry_ids = [uuid.UUID(line["carry_id"]) for line in event.payload["lines"]]
    items = await session.scalars(
        select(KeptItem)
        .where(KeptItem.carry_id.in_(carry_ids), KeptItem.status == "kept")
        .order_by(KeptItem.id)
        .with_for_update()
    )
    await kept.release(session, list(items))


subscribe("intel.forecast_ready", on_forecast_ready)
subscribe(orders_api.CARRY_OVER_EXPIRED, on_carry_over_expired)
