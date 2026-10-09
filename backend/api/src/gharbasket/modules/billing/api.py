"""Facade of M5 Billing & Ledger: the only module other modules may import.

For M4 (spec §3.1, "For other modules"): call these inside your own transaction. Each movement is
keyed by the order (and reason), so a retried cut-off or refund never moves money twice.

For M1's account deletion (spec §3.3, M1 §3.5): `close_wallet` in the deletion's transaction, and
`wallet_closed` before the purge.
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import refunds, service
from gharbasket.modules.billing.events import WALLET_TOPPED_UP
from gharbasket.modules.billing.models import JournalEntry, Payment, Posting
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
    "WALLET_TOPPED_UP",
    "InsufficientBalance",
    "RefundOut",
    "balance",
    "close_wallet",
    "debit_for_order",
    "export_for_user",
    "refund_to_wallet",
    "wallet_closed",
]


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
    """M5's section of the user's data export (M1 §3.3), read-only: wallet entries and top-ups.
    Razorpay ids only; card and UPI details never reach us, and webhook bodies are not exported."""
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
    return {
        "wallet_balance_paise": await balance(session, user_id),
        "wallet_entries": entries,  # into the wallet +, out of it -
        "topups": [payment.as_dict() for payment in topups.scalars()],
    }
