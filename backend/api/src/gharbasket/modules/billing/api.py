"""Facade of M5 Billing & Ledger: the only module other modules may import.

For M4 (spec §3.1, "For other modules"): call these inside your own transaction. Each movement is
keyed by the order (and reason), so a retried cut-off or refund never moves money twice.

For M9 (§3.6): `credit_points` credits Kindness points once per source (a referral, a donation).
"""

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import service
from gharbasket.modules.billing.events import POINTS_CREDITED, WALLET_TOPPED_UP
from gharbasket.modules.billing.kindness import credit_points
from gharbasket.modules.billing.models import JournalEntry, Payment, PointsLot, Posting
from gharbasket.modules.billing.wallet import (
    InsufficientBalance,
    balance,
    debit_for_order,
    refund_to_wallet,
)

__all__ = [
    "POINTS_CREDITED",
    "WALLET_TOPPED_UP",
    "InsufficientBalance",
    "balance",
    "credit_points",
    "debit_for_order",
    "export_for_user",
    "refund_to_wallet",
]


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
