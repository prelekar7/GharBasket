"""Jobs of M5 Billing & Ledger (spec §6).

`send_refunds` (every 5 minutes) sends pending refund-to-source parts to Razorpay (spec §3.3):
- each try is committed before Razorpay hears of it, so a crash or a timeout after the call is
  known; such a part is retried only after listing the payment's refunds and matching our
  `receipt` (the part id), so a lost answer is never refunded twice;
- it retries after 5 and 30 minutes, then leaves the part `pending` and reports it as `stuck`
  for reconciliation. It never marks a part failed: only Razorpay's webhook does;
- dev top-ups' parts are settled at once (`rfnd_dev_…`).

`reconcile_payments` (hourly at :17) compares Razorpay with our records (spec §3.4).
"""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select

from gharbasket.modules.billing import reconciliation, settlement
from gharbasket.modules.billing.models import Payment, RefundPart
from gharbasket.modules.billing.razorpay import RazorpayClient
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.jobs import JobContext, job

BATCH = 50
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
    for _ in range(BATCH):
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
    return await reconciliation.reconcile(
        ctx.session, _razorpay(ctx), ctx.settings, ctx.scheduled_for
    )
