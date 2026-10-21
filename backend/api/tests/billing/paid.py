"""A top-up the refund-to-source code can send money back to (spec §3.3): a paid payment row
and its `topup` entry, for tests that fill a wallet without the Razorpay round trip."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import wallet
from gharbasket.modules.billing.models import JournalEntry, Payment, Refund, RefundPart


async def paid_topup(db: AsyncSession, user: uuid.UUID, amount: int, age_days: int = 0) -> Payment:
    """A `paid` dev top-up created `age_days` ago, credited to the wallet."""
    payment = Payment(
        user_id=user,
        provider="dev",
        amount_paise=amount,
        status="paid",
        provider_order_id=f"order_dev_{uuid.uuid4().hex}",
        provider_payment_id=f"pay_dev_{uuid.uuid4().hex}",
        expires_at=datetime.now(UTC),
        created_at=datetime.now(UTC) - timedelta(days=age_days),
    )
    db.add(payment)
    await db.flush()
    await wallet.credit_topup(
        db, user, amount, payment_ref=payment.provider_payment_id or "", topup_id=payment.id
    )
    return payment


async def order_refund_to_source(
    db: AsyncSession, order_id: uuid.UUID, amount: int, reason: str | None = None
) -> tuple[Refund, list[RefundPart]]:
    """The Refund an order's refund made when the wallet was full (the one for `reason`, or the
    order's only one), with its parts, after checking it is the whole amount and that the
    `refund` entry names it."""
    refunds = select(Refund).where(Refund.order_id == order_id)
    entries = select(JournalEntry).where(
        JournalEntry.ref_id == order_id, JournalEntry.kind == "refund"
    )
    if reason is not None:
        refunds = refunds.where(Refund.idempotency_key == f"order:{order_id}:{reason}")
        entries = entries.where(JournalEntry.idempotency_key == f"refund:{order_id}:{reason}")
    refund = (await db.execute(refunds)).scalar_one()
    parts = list(
        (await db.execute(select(RefundPart).where(RefundPart.refund_id == refund.id))).scalars()
    )
    assert (refund.reason, refund.amount_paise) == ("order", amount)
    assert sum(p.amount_paise for p in parts) == amount
    entry = (await db.execute(entries)).scalar_one()
    assert (entry.details or {}).get("to_source") == str(refund.id)
    return refund, parts
