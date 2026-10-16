"""Facade of M5 Billing & Ledger: the only module other modules may import.

For M4 (spec §3.1, "For other modules"): call these inside your own transaction. Each movement is
keyed by the order (and reason), so a retried cut-off or refund never moves money twice.

For M1's account deletion (spec §3.3, M1 §3.5): `close_wallet` in the deletion's transaction, and
`wallet_closed` before the purge. For M9 (§3.6): `credit_points` credits Kindness points once per
source (a referral, a donation).
"""

import copy
import uuid
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import refunds, service
from gharbasket.modules.billing.events import POINTS_CREDITED, WALLET_TOPPED_UP
from gharbasket.modules.billing.kindness import credit_points
from gharbasket.modules.billing.models import (
    JournalEntry,
    Payment,
    PaymentWebhook,
    PointsLot,
    Posting,
)
from gharbasket.modules.billing.refunds import wallet_closed
from gharbasket.modules.billing.schemas import RefundOut
from gharbasket.modules.billing.wallet import (
    InsufficientBalance,
    balance,
    debit_for_order,
    refund_to_wallet,
)
from gharbasket.platform.settings import Settings, get_settings

__all__ = [
    "POINTS_CREDITED",
    "WALLET_TOPPED_UP",
    "InsufficientBalance",
    "RefundOut",
    "anonymise_for_user",
    "balance",
    "close_wallet",
    "credit_points",
    "debit_for_order",
    "export_for_user",
    "refund_to_wallet",
    "wallet_closed",
]


# What a purged user's stored webhook entities keep (M1 §3.5): what topups.handle_event and the
# refund and reconciliation code read, and nothing that names or reaches a person.
KEPT = frozenset(
    {
        "id",
        "entity",
        "order_id",
        "payment_id",
        "amount",
        "amount_paid",
        "amount_due",
        "amount_refunded",
        "currency",
        "status",
        "method",
        "captured",
        "refund_status",
        "receipt",
        "speed_processed",
        "error_code",
        "error_reason",
        "fee",
        "tax",
        "created_at",
    }
)


async def close_wallet(
    session: AsyncSession,
    user_id: uuid.UUID,
    reason: str = "account_deleted",
    *,
    settings: Settings | None = None,
) -> RefundOut | None:
    """The deleted account's refundable balance becomes one refund to its payments (sent by the
    `send_refunds` job); what no payment can take goes to `unclaimed_balances`. None when the
    wallet is empty or nothing could be refunded."""
    refund = await refunds.close_wallet(session, user_id, reason, settings or get_settings())
    return None if refund is None else await refunds.to_out(session, refund)


async def export_for_user(session: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
    """M5's section of the user's data export (M1 §3.3), read-only: wallet entries, top-ups and
    Kindness points lots. Razorpay ids only; card and UPI details never reach us, and webhook
    bodies are not exported."""
    wallet = await service.find_account(session, "wallet", "user", user_id)
    entries: list[dict[str, Any]] = []
    if wallet is not None:
        rows = await session.execute(
            select(JournalEntry, Posting.amount)
            .join(Posting, Posting.entry_id == JournalEntry.id)
            .where(Posting.account_id == wallet.id)
            .order_by(JournalEntry.id)
        )
        entries = [
            {**entry.as_dict(exclude={"idempotency_key"}), "amount_paise": -amount}
            for entry, amount in rows.all()
        ]
    topups = await session.execute(
        select(Payment).where(Payment.user_id == user_id).order_by(Payment.id)
    )
    lots = await session.execute(
        select(PointsLot).where(PointsLot.user_id == user_id).order_by(PointsLot.id)
    )
    return {
        "wallet_balance_paise": await balance(session, user_id),
        "wallet_entries": entries,  # into the wallet +, out of it -
        "topups": [payment.as_dict() for payment in topups.scalars()],
        "kindness_points": [lot.as_dict(exclude={"entry_id"}) for lot in lots.scalars()],
    }


async def anonymise_for_user(
    session: AsyncSession, user_id: uuid.UUID, household_id: uuid.UUID | None
) -> dict[str, int]:
    """M1's purge (M1 §3.5, order 9). The ledger is append-only and top-ups stay for
    reconciliation. The stored Razorpay webhooks about the user's payments keep only the
    allowlisted fields of each entity (`KEPT`): contact, email, vpa, card and bank details,
    customer and token ids, notes and acquirer data all go, however deeply they are nested."""
    paid = select(Payment.provider_order_id).where(Payment.user_id == user_id)
    refs = select(Payment.provider_payment_id).where(Payment.user_id == user_id)
    body = PaymentWebhook.payload["payload"]
    hooks = await session.scalars(
        select(PaymentWebhook).where(
            or_(
                body["payment"]["entity"]["order_id"].astext.in_(paid),
                body["payment"]["entity"]["id"].astext.in_(refs),
                body["order"]["entity"]["id"].astext.in_(paid),
                body["refund"]["entity"]["payment_id"].astext.in_(refs),
            )
        )
    )
    redacted = 0
    for hook in hooks.all():
        payload = copy.deepcopy(hook.payload)
        for part in (payload.get("payload") or {}).values():
            if isinstance(part, dict) and isinstance(part.get("entity"), dict):
                part["entity"] = {k: v for k, v in part["entity"].items() if k in KEPT}
        if payload != hook.payload:
            hook.payload, redacted = payload, redacted + 1  # a new dict: JSONB sees the change
    return {"payment_webhooks": redacted}
