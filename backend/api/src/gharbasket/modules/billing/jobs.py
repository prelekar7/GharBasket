"""Jobs and consumers of M5 Billing & Ledger (spec §5, §6).

`send_refunds` (every 5 minutes) sends pending refund-to-source parts to Razorpay (spec §3.3):
- each try is committed before Razorpay hears of it, so a crash or a timeout after the call is
  known; such a part is retried only after listing the payment's refunds and matching our
  `receipt` (the part id), so a lost answer is never refunded twice;
- it retries after 5 and 30 minutes, then leaves the part `pending` and reports it as `stuck`
  for reconciliation. It never marks a part failed: only Razorpay's webhook does;
- dev top-ups' parts are settled at once (`rfnd_dev_…`).

`reconcile_payments` (hourly at :17) compares Razorpay with our records (spec §3.4). While
Razorpay is unavailable its billing run is kept `failed` and the job run fails too, so the `ops`
alert hears of it (ADR-0033).

Ops probes (M0 §6.1): `billing.webhook_backlog`, `billing.reconciliation_open` and
`billing.refunds_stuck`.

`weekly_payouts` (Monday 10:00 IST) pays partners what they were owed up to Sunday (spec §3.5,
payouts.py).

Kindness points (§3.6):
- `expire_kindness_points` (daily 00:30 IST) takes back what is left of each lot past its expiry.
- The consumer of M9's `community.donation_verified` credits the donation's points, once per
  donation. It shares the relay's savepoint with the event's other consumers, so a payload it
  cannot use is logged and skipped, never raised.

Revenue (§3.9): the consumer of M4's `orders.delivered` moves what the order still holds in
unearned_revenue to sales_revenue (`revenue.py`), skipping a payload it cannot read as above;
`recognise_delivered_revenue` (manual) runs it again over every relayed delivery. M4's probe
`orders.revenue_unrecognised` reports a delivered order still holding unearned money.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import (
    kindness,
    payouts,
    reconciliation,
    revenue,
    settlement,
)
from gharbasket.modules.billing.models import (
    Payment,
    PaymentWebhook,
    ReconciliationItem,
    RefundPart,
)
from gharbasket.modules.billing.razorpay import RazorpayClient
from gharbasket.platform import ops
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.jobs import JobContext, ProblemsFoundError, job
from gharbasket.platform.models import OutboxEvent
from gharbasket.platform.outbox import subscribe
from gharbasket.platform.pdf import rupees

REFUND_BATCH = 50
RETRY_AFTER = (timedelta(minutes=5), timedelta(minutes=30))
MAX_TRIES = len(RETRY_AFTER) + 1


def _razorpay(ctx: JobContext) -> RazorpayClient | None:
    s = ctx.settings
    if ctx.http is None or s.razorpay_key_id is None or s.razorpay_key_secret is None:
        return None
    return RazorpayClient(ctx.http, s.razorpay_key_id, s.razorpay_key_secret.get_secret_value())


async def _send(razorpay: RazorpayClient, payment_ref: str, part: RefundPart) -> tuple[str, bool]:
    """(refund id, found): a retry first looks for the refund an earlier try may have made."""
    receipt = str(part.id)
    if part.attempts > 1:
        for item in await razorpay.refunds(payment_ref):
            if item.get("receipt") == receipt:
                return str(item["id"]), True
    return await razorpay.refund(payment_ref, part.amount_paise, receipt=receipt), False


@job("send_refunds", "Send pending refund-to-source parts to Razorpay, receipt-matched on retry")
async def send_refunds(ctx: JobContext) -> dict[str, Any]:
    session, now = ctx.session, ctx.scheduled_for
    razorpay = _razorpay(ctx)
    counts = {"sent": 0, "found": 0, "dev_settled": 0, "retrying": 0, "unavailable": 0}
    seen: list[Any] = []
    for _ in range(REFUND_BATCH):
        row = (
            await session.execute(
                select(RefundPart, Payment)
                .join(Payment, Payment.id == RefundPart.payment_id)
                .where(
                    RefundPart.status == "pending",
                    RefundPart.attempts < MAX_TRIES,
                    or_(RefundPart.next_attempt_at.is_(None), RefundPart.next_attempt_at <= now),
                    RefundPart.id.not_in(seen),
                )
                .order_by(RefundPart.id)
                .limit(1)
                .with_for_update(of=RefundPart, skip_locked=True)
            )
        ).first()
        if row is None:
            break
        part, payment = row
        seen.append(part.id)
        if payment.provider == "dev":
            part.status, part.provider_refund_id = "processed", f"rfnd_dev_{part.id.hex[-14:]}"
            part.sent_at = part.settled_at = datetime.now(UTC)
            await settlement.settle(session, part.refund_id)
            await session.commit()
            counts["dev_settled"] += 1
            continue
        if razorpay is None or payment.provider_payment_id is None:
            counts["unavailable"] += 1  # no keys here: the part waits, untried
            continue
        part.attempts += 1
        later = RETRY_AFTER[part.attempts - 1] if part.attempts <= len(RETRY_AFTER) else None
        part.next_attempt_at = now + later if later else None
        await settlement.mark_processing(session, part.refund_id)
        await session.commit()  # the try is on record before Razorpay hears of it
        try:
            refund_ref, found = await _send(razorpay, payment.provider_payment_id, part)
        except ProblemError:
            await session.refresh(part, with_for_update=True)
            part.last_error = "razorpay_unavailable"
            await session.commit()
            counts["retrying"] += 1
            continue
        await session.refresh(part, with_for_update=True)
        if part.status == "pending":  # the webhook may have settled it in the meantime
            part.status, part.sent_at = "sent", datetime.now(UTC)
        part.provider_refund_id, part.last_error = refund_ref, None
        await session.commit()
        counts["found" if found else "sent"] += 1
    stuck = await session.execute(
        select(func.count()).where(RefundPart.status == "pending", RefundPart.attempts >= MAX_TRIES)
    )
    return {**counts, "stuck": stuck.scalar_one()}


@job("reconcile_payments", "Compare Razorpay's payments and refunds with ours; heal lost webhooks")
async def reconcile_payments(ctx: JobContext) -> dict[str, Any]:
    summary = await reconciliation.reconcile(
        ctx.session, _razorpay(ctx), ctx.settings, ctx.scheduled_for
    )
    if summary["status"] == "failed":
        # Commit the billing run (`failed`, nothing changed) before the job fails, or the job's
        # rollback would take it away; a run without keys is `skipped` and stays an ok job.
        await ctx.session.commit()
        raise ProblemsFoundError(["Razorpay is unavailable; nothing was compared"])
    return summary


# --- ops probes (M0 §6.1, spec §3.4) ------------------------------------------------------------

WEBHOOK_BACKLOG_AFTER = timedelta(minutes=15)
RECON_OPEN_AFTER = timedelta(hours=24)


@ops.probe("billing.webhook_backlog")
async def webhook_backlog(session: AsyncSession, now: datetime) -> list[str]:
    # The webhook endpoint stores and processes in one transaction, so this stays empty unless
    # processing ever moves out of the request.
    waiting = (
        await session.execute(
            select(func.count()).where(
                PaymentWebhook.processed_at.is_(None),
                PaymentWebhook.received_at < now - WEBHOOK_BACKLOG_AFTER,
            )
        )
    ).scalar_one()
    return [f"{waiting} webhooks unprocessed for > 15 min"] if waiting else []


@ops.probe("billing.reconciliation_open")
async def reconciliation_open(session: AsyncSession, now: datetime) -> list[str]:
    rows = await session.execute(
        select(ReconciliationItem.kind, func.count())
        .where(
            ReconciliationItem.status == "open",
            ReconciliationItem.first_seen_at < now - RECON_OPEN_AFTER,
        )
        .group_by(ReconciliationItem.kind)
        .order_by(ReconciliationItem.kind)
    )
    counts = rows.all()
    if not counts:
        return []
    kinds = ", ".join(f"{kind} x{n}" for kind, n in counts)
    total = sum(n for _, n in counts)
    return [f"{total} reconciliation items open for > 24 h ({kinds}); see /reconciliation"]


@ops.probe("billing.refunds_stuck")
async def refunds_stuck(session: AsyncSession, now: datetime) -> list[str]:
    parts, paise = (
        await session.execute(
            select(func.count(), func.coalesce(func.sum(RefundPart.amount_paise), 0)).where(
                RefundPart.status == "pending", RefundPart.attempts >= MAX_TRIES
            )
        )
    ).one()
    if not parts:
        return []
    total = rupees(int(paise))  # SUM of a bigint is numeric
    return [f"{parts} refund parts stuck after {MAX_TRIES} tries ({total})"]


@job("weekly_payouts", "Pay partners what they were owed up to last Sunday (IST), if eligible")
async def weekly_payouts(ctx: JobContext) -> dict[str, Any]:
    """Monday 10:00 IST (spec §3.5): the ISO week before `scheduled_for`, through the simulated
    provider in the prototype. A rerun pays nothing twice: one row per (partner, week), and its
    entries are keyed by it."""
    return await payouts.run_weekly(ctx.session, ctx.settings, ctx.scheduled_for)


# M9's event (M9 §3.4, §5): { donation_id, user_id, household_id, hub_id, society_id, drive_id,
# points }. Named here rather than imported, so M5 does not depend on M9.
DONATION_VERIFIED = "community.donation_verified"
BATCH = 200
log = structlog.get_logger("gharbasket.billing.kindness")


@job("expire_kindness_points", "Take back the Kindness points of lots past their expiry")
async def expire_kindness_points(ctx: JobContext) -> dict[str, Any]:
    at = ctx.scheduled_for  # lots expire at the end of an IST day; the job runs at 00:30
    users = lots = points = 0
    last: uuid.UUID | None = None
    while batch := await kindness.users_with_expired_lots(ctx.session, at, after=last, limit=BATCH):
        for user_id in batch:
            expired, taken = await kindness.expire_lots(ctx.session, user_id, at)
            users, lots, points = users + 1, lots + expired, points + taken
            last = user_id
        await ctx.session.commit()
    return {"users": users, "lots": lots, "points": points}


def _donation(payload: dict[str, Any]) -> tuple[uuid.UUID, uuid.UUID, int] | None:
    try:
        donation_id = uuid.UUID(str(payload["donation_id"]))
        user_id = uuid.UUID(str(payload["user_id"]))
        points = payload["points"]
    except (KeyError, TypeError, ValueError):
        return None
    if type(points) is not int or points <= 0:
        return None
    return donation_id, user_id, points


async def on_donation_verified(session: AsyncSession, event: OutboxEvent) -> None:
    """Credit a verified donation's points (`credit_points(…, "donation", donation_id)`); a
    redelivery credits nothing more. A donation that earned 0 points credits nothing."""
    payload = event.payload if isinstance(event.payload, dict) else {}
    found = _donation(payload)  # pyright: ignore[reportUnknownArgumentType]
    if found is None:
        if payload.get("points") != 0:  # pyright: ignore[reportUnknownMemberType]
            log.warning("donation_verified_unusable", event_id=str(event.id))
        return
    donation_id, user_id, points = found
    try:
        async with session.begin_nested():
            await kindness.credit_points(session, user_id, points, "donation", donation_id)
    except ProblemError as problem:  # the same donation again with other points
        log.warning(
            "donation_verified_conflict",
            event_id=str(event.id),
            donation_id=str(donation_id),
            code=problem.code,
        )


subscribe(DONATION_VERIFIED, on_donation_verified)


# --- revenue recognition (spec §3.9) -------------------------------------------------------------

# M4's event: { order_id, delivery_date, kind, …, carried: [{ carry_id, from_order_id, product_id,
# paid_paise }] }. Named here rather than imported, so M5 does not depend on M4.
ORDER_DELIVERED = "orders.delivered"


async def on_order_delivered(session: AsyncSession, event: OutboxEvent) -> None:
    """Recognise the delivered order's unearned money, and its carried items' on their missed
    orders; once per key, so a redelivery of the event posts nothing more."""
    await revenue.recognise_delivery(session, event.payload, event.id)


subscribe(ORDER_DELIVERED, on_order_delivered)


@job("recognise_delivered_revenue", "Backfill: recognise revenue for every relayed delivery")
async def recognise_delivered_revenue(ctx: JobContext) -> dict[str, Any]:
    """The consumer again over every `orders.delivered` already relayed, oldest first, in
    batches; the keys make a rerun post nothing twice (manual, after the deploy of §3.9)."""
    events = recognised = paise = 0
    last: uuid.UUID | None = None
    while True:
        query = select(OutboxEvent).where(
            OutboxEvent.event_type == ORDER_DELIVERED, OutboxEvent.sent_at.is_not(None)
        )
        if last is not None:
            query = query.where(OutboxEvent.id > last)
        batch = list(await ctx.session.scalars(query.order_by(OutboxEvent.id).limit(BATCH)))
        if not batch:
            break
        for event in batch:
            posted = await revenue.recognise_delivery(ctx.session, event.payload, event.id)
            events, recognised, paise = events + 1, recognised + (posted > 0), paise + posted
            last = event.id
        await ctx.session.commit()
    return {"events": events, "recognised": recognised, "paise": paise}
