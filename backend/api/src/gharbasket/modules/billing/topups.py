"""Wallet top-ups (spec §3.1, handbook 07 W3): a Razorpay order, Checkout in the app, then the
webhook credits the wallet. Never the app: its "success" is only a hint to start polling.

- An unpaid top-up holds its amount against the ₹10,000 cap and the ₹20,000 monthly allowance
  for 30 minutes, then shows as "expired".
- A payment is credited once, whichever event (or dev payment) reports it first.
- A payment that no longer fits under the cap (it arrived late) is refunded to its source.
- Without Razorpay keys on a developer machine or in tests, top-ups are "dev" orders, paid by
  POST /v1/dev/razorpay/pay through the same processing as a real webhook.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import events, service, wallet
from gharbasket.modules.billing.models import Payment
from gharbasket.modules.billing.razorpay import RazorpayClient, payments_unavailable
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.outbox import publish

HOLD = timedelta(minutes=30)
MIN_PAISE = 10_000  # ₹100
DEV_KEY_ID = "rzp_test_dev"
log = structlog.get_logger("gharbasket.billing.topups")


def topup_not_found() -> ProblemError:
    return ProblemError(404, "billing.topup_not_found", "No such top-up.")


def status_of(payment: Payment, now: datetime | None = None) -> str:
    if payment.status == "created" and payment.expires_at <= (now or datetime.now(UTC)):
        return "expired"
    return payment.status


def _limit(code: str, title: str, room: int) -> ProblemError:
    return ProblemError(422, code, title, extra={"room_paise": max(0, room)})


@dataclass(frozen=True)
class Created:
    payment: Payment
    key_id: str


async def create_topup(
    session: AsyncSession,
    user_id: uuid.UUID,
    amount_paise: int,
    razorpay: RazorpayClient | None,
    *,
    dev_orders: bool,
) -> Created:
    if amount_paise < MIN_PAISE or amount_paise % 100:
        raise ProblemError(422, "billing.topup_too_small", "Top up at least ₹100, in whole rupees.")
    # The wallet row lock makes two top-ups started at once check the limits one at a time.
    account = await service.owned_account(
        session, "wallet", "user", user_id, kind="liability", lock=True
    )
    held = await wallet.held_paise(session, user_id)
    room = wallet.CAP_PAISE - await service.balance(session, account) - held
    if amount_paise > room:
        raise _limit("billing.wallet_limit", "The wallet can hold at most ₹10,000.", room)
    month = wallet.MONTHLY_TOPUP_PAISE - await wallet.topped_up_this_month(session, account) - held
    if amount_paise > month:
        raise _limit(
            "billing.monthly_topup_limit", "Top-ups are limited to ₹20,000 a month.", month
        )
    if razorpay is None and not dev_orders:
        raise payments_unavailable()

    payment = Payment(
        user_id=user_id,
        provider="razorpay" if razorpay else "dev",
        amount_paise=amount_paise,
        status="created",
        expires_at=datetime.now(UTC) + HOLD,
    )
    session.add(payment)
    await session.flush()
    if razorpay is not None:
        payment.provider_order_id = await razorpay.create_order(
            amount_paise, receipt=str(payment.id), notes={"topup_id": str(payment.id)}
        )
        key_id = razorpay.key_id
    else:
        payment.provider_order_id = f"order_dev_{payment.id.hex[-14:]}"
        key_id = DEV_KEY_ID
    await session.flush()
    await session.refresh(payment)
    return Created(payment, key_id)


async def get_topup(session: AsyncSession, user_id: uuid.UUID, topup_id: uuid.UUID) -> Payment:
    payment = await session.get(Payment, topup_id)
    if payment is None or payment.user_id != user_id:  # someone else's looks like no top-up
        raise topup_not_found()
    return payment


async def by_order(session: AsyncSession, order_id: str) -> Payment | None:
    return (
        await session.execute(
            select(Payment).where(Payment.provider_order_id == order_id).with_for_update()
        )
    ).scalar_one_or_none()


async def handle_event(
    session: AsyncSession, event: dict[str, Any], razorpay: RazorpayClient | None
) -> str:
    """Apply one Razorpay event; returns what happened (stored with the webhook)."""
    kind = event.get("event")
    if kind not in ("payment.captured", "payment.failed"):
        return "ignored"
    try:
        entity = event["payload"]["payment"]["entity"]
        order_id, payment_ref = str(entity["order_id"]), str(entity["id"])
        amount, currency = int(entity["amount"]), str(entity["currency"])
    except (KeyError, TypeError, ValueError):
        return "malformed"
    payment = await by_order(session, order_id)
    if payment is None:
        return "unknown_order"
    if kind == "payment.failed":
        if payment.status == "created":
            payment.status = "failed"  # Checkout may still retry and capture on this order
        return "failed"
    if amount != payment.amount_paise or currency != "INR":
        log.warning("topup_amount_mismatch", topup_id=str(payment.id))
        return "amount_mismatch"  # never credited; reconciliation flags it
    if payment.status in ("paid", "refunded"):
        return "already_settled"
    return await _settle(session, payment, payment_ref, razorpay)


async def _settle(
    session: AsyncSession, payment: Payment, payment_ref: str, razorpay: RazorpayClient | None
) -> str:
    payment.provider_payment_id = payment_ref
    try:
        async with session.begin_nested():
            entry, _ = await wallet.credit_topup(
                session,
                payment.user_id,
                payment.amount_paise,
                payment_ref=payment_ref,
                topup_id=payment.id,
            )
    except ProblemError as problem:
        if problem.code != "billing.wallet_limit":
            raise
        # Paid too late to fit under the cap: the money goes back where it came from.
        if payment.provider == "razorpay":
            if razorpay is None:
                raise payments_unavailable() from problem
            payment.provider_refund_id = await razorpay.refund(payment_ref, payment.amount_paise)
        payment.status = "refunded"
        return "refunded_over_cap"
    payment.status = "paid"
    await publish(
        session,
        events.WALLET_TOPPED_UP,
        {
            "user_id": str(payment.user_id),
            "topup_id": str(payment.id),
            "amount_paise": payment.amount_paise,
            "journal_entry_id": str(entry.id),
        },
    )
    return "credited"


def dev_capture(payment: Payment) -> dict[str, Any]:
    """The `payment.captured` event Razorpay would send for this dev order."""
    return {
        "event": "payment.captured",
        "payload": {
            "payment": {
                "entity": {
                    "id": f"pay_dev_{uuid.uuid4().hex[:14]}",
                    "order_id": payment.provider_order_id,
                    "amount": payment.amount_paise,
                    "currency": "INR",
                    "status": "captured",
                }
            }
        },
    }
