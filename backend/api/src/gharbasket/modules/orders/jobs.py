"""The cut-off handshake of M4 (spec §3.1, ADR-0021): `cutoff_lock` at T-1 21:00 IST debits the
wallet and locks each approved order for delivery day T (or marks it `payment_failed` with a
two-hour grace), `payment_grace_expiry` at T-1 23:00 cancels what is still unpaid, and a top-up
inside the grace period re-runs the debit (consumer of `billing.wallet_topped_up`).

`scheduled_for` decides the delivery date, never the clock, so a late run does the same work.
`expire_carry_overs` (02:40 IST) refunds carried items still waiting 14 days after the miss
(§3.8). The ops probe `orders.revenue_unrecognised` asks M5 whether recently delivered or closed
refunded orders still hold unearned money (M5 §3.9); `republish_closed_orders` (manual) sends
`orders.closed` again for refunded orders closed before the event existed (§3.8).
"""

import uuid
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.orders import carry, service, state
from gharbasket.modules.orders.models import Order, OrderEvent
from gharbasket.platform import ops
from gharbasket.platform.jobs import JobContext, job
from gharbasket.platform.models import OutboxEvent
from gharbasket.platform.outbox import subscribe

IST = ZoneInfo("Asia/Kolkata")
BATCH = 200
GRACE = service.PAYMENT_GRACE
log = structlog.get_logger("gharbasket.orders.cutoff")


def delivery_day(ctx: JobContext) -> date:
    """T-1 evening jobs work for the next day's deliveries."""
    return ctx.scheduled_for.astimezone(IST).date() + timedelta(days=1)


async def _orders(
    session: AsyncSession, delivery_date: date, status: str, *, after: uuid.UUID | None = None
) -> list[Order]:
    query = (
        select(Order)
        .where(Order.delivery_date == delivery_date, Order.status == status)
        .order_by(Order.id)
        .limit(BATCH)
        .with_for_update(skip_locked=True)
    )
    if after is not None:
        query = query.where(Order.id > after)
    return list((await session.execute(query)).scalars())


async def lock_or_fail(session: AsyncSession, order: Order, *, grace_until: str) -> str:
    """Debit the household owner's wallet for one order (keyed by the order, so a retry never
    debits twice) and move it on. Returns the new status."""
    try:
        await service.debit_and_lock(session, order, actor="system")
    except billing_api.InsufficientBalance as short:
        if order.status != "payment_failed":  # a top-up that still falls short changes nothing
            await service.transition(
                session,
                order,
                "payment_failed",
                actor="system",
                payload={
                    "shortfall_paise": order.total_paise
                    - int((short.extra or {})["balance_paise"]),
                    "grace_until": grace_until,
                },
            )
        return "payment_failed"
    return "locked"


@job("cutoff_lock", "Debit and lock tomorrow's approved orders; unpaid ones get a 2 h grace")
async def cutoff_lock(ctx: JobContext) -> dict[str, Any]:
    on = delivery_day(ctx)
    grace_until = (ctx.scheduled_for + GRACE).isoformat()
    counts: dict[str, Any] = {"locked": 0, "payment_failed": 0, "skipped_already_locked": 0}
    already = await ctx.session.execute(
        select(Order.id).where(Order.delivery_date == on, Order.status.in_(state.PAID))
    )
    counts["skipped_already_locked"] = len(already.all())
    last: uuid.UUID | None = None
    while True:
        batch = await _orders(ctx.session, on, "approved", after=last)
        if not batch:
            break
        for order in batch:
            counts[await lock_or_fail(ctx.session, order, grace_until=grace_until)] += 1
            last = order.id
        await ctx.session.commit()
    counts["delivery_date"] = on.isoformat()
    return counts


@job("expire_carry_overs", "Refund carried items still waiting 14 days after the miss, no fee")
async def expire_carry_overs(ctx: JobContext) -> dict[str, Any]:
    """Every `pending` row that expired before the run's day is refunded at its paid price, once
    (the refund key is per row); a wallet at its cap leaves the row for the next run."""
    return await carry.expire(ctx.session, ctx.scheduled_for.astimezone(IST).date())


@job("payment_grace_expiry", "Cancel tomorrow's orders still unpaid after the grace period")
async def payment_grace_expiry(ctx: JobContext) -> dict[str, Any]:
    on = delivery_day(ctx)
    cancelled = 0
    for order in await _orders(ctx.session, on, "payment_failed"):
        await service.cancel(ctx.session, order, actor="system", reason="payment")
        cancelled += 1
    return {"cancelled": cancelled, "delivery_date": on.isoformat()}


async def on_wallet_topped_up(session: AsyncSession, event: OutboxEvent) -> None:
    """A top-up during the grace period pays for the household's unpaid orders, as `pay` does
    (§3.4): same debit key, same row lock, so the two never charge twice. Handled after an
    order's grace ended, it leaves the order to `payment_grace_expiry`, as `pay` refuses it."""
    at = service.now()
    user_id = uuid.UUID(event.payload["user_id"])
    household = await identity_api.household_for_user(session, user_id)
    if household is None:
        return
    unpaid = list(
        (
            await session.execute(
                select(Order)
                .where(Order.household_id == household.id, Order.status == "payment_failed")
                .order_by(Order.delivery_date, Order.id)
                .with_for_update()
            )
        ).scalars()
    )
    for order in unpaid:
        if at >= order.cutoff_at + GRACE:
            continue
        grace_until = (order.cutoff_at + GRACE).isoformat()
        outcome = await lock_or_fail(session, order, grace_until=grace_until)
        if outcome == "payment_failed":
            break  # the top-up did not cover this one; the grace job decides
        log.info("order_paid_after_topup", order_id=str(order.id), user_id=str(user_id))


subscribe(billing_api.WALLET_TOPPED_UP, on_wallet_topped_up)


# --- ops probe (M0 §6.1, M5 §3.9) ----------------------------------------------------------------

REVENUE_AFTER = timedelta(hours=1)  # M5's consumer of orders.delivered runs within seconds
REVENUE_WINDOW = timedelta(days=7)
CLOSED_AFTER = timedelta(days=1)  # a refunded order's `orders.closed` is consumed within seconds


@ops.probe("orders.revenue_unrecognised")
async def revenue_unrecognised(session: AsyncSession, now: datetime) -> list[str]:
    """Orders that still hold money in M5's unearned_revenue after M5 should have recognised it:
    delivered more than an hour ago (and within 7 days), on `orders.delivered`; or `refunded` and
    closed more than a day ago (within 7 days), on `orders.closed` (the second-miss fee, the rest of
    a partial refund). With the consumers working, none. A missed order that still has carried
    rows open is not closed, so it is left out (M5 §3.9)."""
    delivered = list(
        await session.scalars(
            select(OrderEvent.order_id)
            .where(
                OrderEvent.to_status == "delivered",
                OrderEvent.at < now - REVENUE_AFTER,
                OrderEvent.at >= now - REVENUE_WINDOW,
            )
            .distinct()
        )
    )
    closed = await carry.closed_order_ids(session, now - REVENUE_WINDOW, now - CLOSED_AFTER)
    out: list[str] = []
    for ids, adjective, job_name in (
        (delivered, "delivered", "recognise_delivered_revenue"),
        (closed, "refunded", "republish_closed_orders"),
    ):
        held = await billing_api.held_paise(session, ids)
        left = sum(1 for paise in held.values() if paise > 0)
        if left:
            orders = (
                f"1 {adjective} order still holds"
                if left == 1
                else f"{left} {adjective} orders still hold"
            )
            out.append(f"{orders} unearned revenue; run {job_name}")
    return out


@job(
    "republish_closed_orders",
    "Backfill: orders.closed again for refunded orders still holding money",
)
async def republish_closed_orders(ctx: JobContext) -> dict[str, Any]:
    """Publishes `orders.closed` for every closed `refunded` order that still holds money in M5
    (asked of `billing.api.held_paise`), oldest id first, in batches; M5's consumer recognises it.
    For orders refunded before the event existed; an order holding nothing or still open is
    skipped, and a rerun posts nothing twice (M5's key). Manual (M4 §3.8)."""
    seen = published = 0
    last: uuid.UUID | None = None
    while True:
        batch = await carry.closed_orders(ctx.session, after=last, limit=BATCH)
        if not batch:
            break
        held = await billing_api.held_paise(ctx.session, [order.id for order in batch])
        for order in batch:
            seen += 1
            if held.get(order.id, 0) > 0:
                await carry.publish_closed(ctx.session, order)
                published += 1
        last = batch[-1].id
        await ctx.session.commit()
    return {"closed": seen, "published": published}
