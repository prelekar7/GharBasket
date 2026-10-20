"""Scheduled jobs of M3 (spec §6), registered with the platform's jobs runner (ADR-0021).

`generate_weekly_plans` runs at T-3 18:00 IST for delivery day T: `scheduled_for` decides the
delivery date (three days later, IST), never the clock, and the boxes are drafts.
`send_box_ready_notices` (T-3 18:30) opens them to the customer and publishes
`subscriptions.box_ready`; `remind_unapproved` (T-1 19:00) publishes `subscriptions.cutoff_reminder`
for boxes nothing will approve otherwise. M11 turns both into messages. `apply_auto_approve` runs
at T-1 20:55 IST, five minutes before M4's cut-off, for the next day's deliveries.

The cycle mirrors its order after the cut-off: `orders.locked` → `locked` (with `order_id`),
`orders.payment_failed` → `payment_failed` (§3.3), `orders.cancelled` with reason `payment` →
`cancelled`.
"""

import uuid
from datetime import date, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community import api as community_api
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.subscriptions import service
from gharbasket.platform.jobs import JobContext, job
from gharbasket.platform.models import OutboxEvent
from gharbasket.platform.outbox import subscribe

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
    "send_box_ready_notices",
    "Show the drafted boxes for the delivery day three days on and tell their households",
)
async def send_box_ready_notices(ctx: JobContext) -> dict[str, Any]:
    delivery_date = ctx.scheduled_for.astimezone(IST).date() + timedelta(days=DAYS_AHEAD)
    counts: dict[str, Any] = await service.send_box_ready_notices(
        ctx.session, delivery_date, ctx.scheduled_for
    )
    counts["delivery_date"] = delivery_date.isoformat()
    return counts


@job(
    "remind_unapproved",
    "Remind households whose box for tomorrow is still unapproved and will not auto-approve",
)
async def remind_unapproved(ctx: JobContext) -> dict[str, Any]:
    delivery_date = ctx.scheduled_for.astimezone(IST).date() + timedelta(days=1)
    counts: dict[str, Any] = await service.remind_unapproved(
        ctx.session, delivery_date, ctx.scheduled_for
    )
    counts["delivery_date"] = delivery_date.isoformat()
    return counts


@job(
    "apply_auto_approve",
    "Approve tomorrow's boxes for households that asked for it (notice sent 24 h before)",
)
async def apply_auto_approve(ctx: JobContext) -> dict[str, Any]:
    delivery_date = ctx.scheduled_for.astimezone(IST).date() + timedelta(days=1)
    counts: dict[str, Any] = await service.auto_approve_due(ctx.session, delivery_date)
    counts["delivery_date"] = delivery_date.isoformat()
    return counts


MIRRORED = {"order_id", "household_id", "delivery_date"}


def _about_the_box(event: OutboxEvent) -> bool:
    """A box order's event with what the cycle mirrors; a tiffin (M4 §3.5) on the same date is
    another order and never moves the week."""
    return event.payload.get("kind", "box") == "box" and event.payload.keys() >= MIRRORED


async def on_order_locked(session: AsyncSession, event: OutboxEvent) -> None:
    if not _about_the_box(event):
        return
    cycle = await service.cycle_for(
        session,
        uuid.UUID(event.payload["household_id"]),
        date.fromisoformat(event.payload["delivery_date"]),
    )
    if cycle is not None:
        await service.mark_locked(session, cycle, uuid.UUID(event.payload["order_id"]))


async def on_order_payment_failed(session: AsyncSession, event: OutboxEvent) -> None:
    if not _about_the_box(event):
        return
    cycle = await service.cycle_for(
        session,
        uuid.UUID(event.payload["household_id"]),
        date.fromisoformat(event.payload["delivery_date"]),
    )
    if cycle is not None and cycle.order_id == uuid.UUID(event.payload["order_id"]):
        await service.mark_payment_failed(session, cycle)


async def on_order_cancelled(session: AsyncSession, event: OutboxEvent) -> None:
    if event.payload.get("reason") != "payment" or not _about_the_box(event):
        return  # a skip or a customer cancellation already changed the cycle
    cycle = await service.cycle_for(
        session,
        uuid.UUID(event.payload["household_id"]),
        date.fromisoformat(event.payload["delivery_date"]),
    )
    if cycle is not None and cycle.status in (*service.EDITABLE, "payment_failed"):
        cycle.status = "cancelled"
        cycle.version += 1
        await session.flush()


async def on_deletion_requested(session: AsyncSession, event: OutboxEvent) -> None:
    """M1 §3.5: the subscription ends and the weeks not yet paid are skipped."""
    try:
        household_id = uuid.UUID(str(event.payload["household_id"]))
    except (KeyError, ValueError):
        return  # no household: nothing to end
    await service.end_for_deletion(session, household_id)


subscribe(orders_api.LOCKED, on_order_locked)
subscribe(orders_api.PAYMENT_FAILED, on_order_payment_failed)
subscribe(orders_api.CANCELLED, on_order_cancelled)
subscribe(identity_api.DELETION_REQUESTED, on_deletion_requested)
