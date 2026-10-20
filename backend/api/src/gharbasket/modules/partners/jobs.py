"""Jobs of M8 Partner Network (spec §6, ADR-0021). `scheduled_for` decides the delivery day T,
never the clock, and stands in for "now" in the rounds' reply deadlines.

- `assign_kitchens` (T-1 21:30 IST): round 1 for every active hub, both meals of T.
- `assignment_acceptance_timeout` (every 15 minutes, T-1 21:45 to T 00:30 IST): expires offers past
  `respond_by`, then runs the next round for released and late-locked orders; no new round
  starts after T 00:00.
- `refund_unassigned_tiffins` (T 09:30 IST for lunch, ready_by minus 2 hours for a dinner slot):
  locked tiffins of T that no assignment holds are refunded in full through M4.

- `prompt_tiffin_ratings` (T 14:30 IST): `partners.rating_requested` for the day's delivered lunch
  tiffins that nobody asked about yet and that are not rated.

Consumers (§5): M4's `orders.dispatched`, `orders.delivered` and `orders.delivery_failed` of tiffins
move the assignment's orders along and complete it, and `orders.delivered`, `orders.dispatched` and
`orders.packed` post what the work earned (`earnings`); `identity.deletion_requested` makes her
partner records `left`.
"""

import uuid
from datetime import date, time, timedelta
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.hub_ops import api as hub_ops_api
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.partners import assignments, cooking, earnings, fulfilment, ratings, service
from gharbasket.platform.jobs import JobContext, job
from gharbasket.platform.models import OutboxEvent
from gharbasket.platform.outbox import subscribe

log = structlog.get_logger("gharbasket.partners")
NOON = time(12, 0)  # a run in the evening works for tomorrow, one after midnight for today


async def _rounds(ctx: JobContext, day: date) -> dict[str, Any]:
    """Each hub's round in its own savepoint: one hub's failure is logged and listed in the
    summary as `failed_hubs`, and the other hubs' offers stand."""
    offers = unassigned = runs = 0
    failed: list[str] = []
    for hub_id in await hub_ops_api.active_hub_ids(ctx.session):
        try:
            async with ctx.session.begin_nested():
                result = await assignments.run_round(
                    ctx.session, ctx.http, ctx.settings, hub_id, day, at=ctx.scheduled_for
                )
        except Exception as exc:
            log.error("assignment_round_failed", hub_id=str(hub_id), error=type(exc).__name__)
            failed.append(str(hub_id))
            continue
        runs, offers = runs + len(result.run_ids), offers + result.offers
        unassigned += result.unassigned
    return {"runs": runs, "offers": offers, "unassigned": unassigned} | (
        {"failed_hubs": failed} if failed else {}
    )


@job("assign_kitchens", "Round 1 of tomorrow's tiffin assignment at every active hub (21:30 IST)")
async def assign_kitchens(ctx: JobContext) -> dict[str, Any]:
    day = ctx.scheduled_for.astimezone(assignments.IST).date() + timedelta(days=1)
    return {"delivery_date": day.isoformat(), **await _rounds(ctx, day)}


@job(
    "assignment_acceptance_timeout",
    "Expire unanswered tiffin offers and run the next round (every 15 min, 21:45 to 00:30 IST)",
)
async def assignment_acceptance_timeout(ctx: JobContext) -> dict[str, Any]:
    """Per hub, in its own savepoint and in the one lock order everything uses (the hub's lock,
    then its offer rows): lock, expire its overdue offers, then its round while T 00:00 is ahead.
    Offers of hubs that are no longer active are expired last, each under its hub's lock."""
    local = ctx.scheduled_for.astimezone(assignments.IST)
    day = local.date() + timedelta(days=1) if local.time() >= NOON else local.date()
    before_midnight = local.date() < day
    expired = runs = offers = unassigned = 0
    failed: list[str] = []
    active = await hub_ops_api.active_hub_ids(ctx.session)
    for hub_id in active:
        try:
            async with ctx.session.begin_nested():
                await assignments.lock_hub(ctx.session, hub_id)
                done = await assignments.expire_overdue(ctx.session, ctx.scheduled_for, hub_id)
                result = None
                if before_midnight:
                    result = await assignments.run_round(
                        ctx.session, ctx.http, ctx.settings, hub_id, day, at=ctx.scheduled_for
                    )
        except Exception as exc:
            log.error("assignment_round_failed", hub_id=str(hub_id), error=type(exc).__name__)
            failed.append(str(hub_id))
            continue
        expired += done
        if result is not None:
            runs, offers = runs + len(result.run_ids), offers + result.offers
            unassigned += result.unassigned
    for hub_id in await assignments.overdue_hubs(ctx.session, ctx.scheduled_for):
        if hub_id not in active:
            await assignments.lock_hub(ctx.session, hub_id)
            expired += await assignments.expire_overdue(ctx.session, ctx.scheduled_for, hub_id)
    summary: dict[str, Any] = {"delivery_date": day.isoformat(), "expired": expired}
    if before_midnight:
        summary |= {"runs": runs, "offers": offers, "unassigned": unassigned}
    return summary | ({"failed_hubs": failed} if failed else {})


@job(
    "refund_unassigned_tiffins",
    "Refund locked tiffins no kitchen took once manual assignment closed (T 09:30 for lunch)",
)
async def refund_unassigned_tiffins(ctx: JobContext) -> dict[str, Any]:
    """Each hub in its own savepoint: a failing hub is logged and listed as `failed_hubs`, and
    the other hubs' refunds stand."""
    day = ctx.scheduled_for.astimezone(assignments.IST).date()
    refunded = refused = 0
    failed_hubs: list[str] = []
    for hub_id in await hub_ops_api.active_hub_ids(ctx.session):
        try:
            async with ctx.session.begin_nested():
                done, failed = await cooking.refund_unassigned(
                    ctx.session, hub_id, day, ctx.scheduled_for
                )
        except Exception as exc:
            log.error("tiffin_refund_hub_failed", hub_id=str(hub_id), error=type(exc).__name__)
            failed_hubs.append(str(hub_id))
            continue
        refunded, refused = refunded + done, refused + failed
    return {"delivery_date": day.isoformat(), "refunded": refunded, "refused": refused} | (
        {"failed_hubs": failed_hubs} if failed_hubs else {}
    )


@job("prompt_tiffin_ratings", "Ask households to rate today's delivered lunch tiffins (14:30 IST)")
async def prompt_tiffin_ratings(ctx: JobContext) -> dict[str, Any]:
    day = ctx.scheduled_for.astimezone(assignments.IST).date()
    return {
        "delivery_date": day.isoformat(),
        "prompted": await ratings.prompt(ctx.session, day, "lunch"),
    }


async def on_deletion_requested(session: AsyncSession, event: OutboxEvent) -> None:
    """M1 §3.5: the request, not the purge 30 days later, because she can no longer sign in."""
    try:
        user_id = uuid.UUID(str(event.payload["user_id"]))
    except (KeyError, ValueError):
        return
    await service.leave_for_user(session, user_id)


for _event in (orders_api.DISPATCHED, orders_api.DELIVERED, orders_api.DELIVERY_FAILED):
    subscribe(_event, fulfilment.on_order_event)
for _event in (orders_api.DELIVERED, orders_api.DISPATCHED, orders_api.PACKED):
    subscribe(_event, earnings.on_order_event)
subscribe(identity_api.DELETION_REQUESTED, on_deletion_requested)
