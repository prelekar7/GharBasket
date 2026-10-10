"""Facade of M5 Billing & Ledger: the only module other modules may import.

For M4 (spec §3.1, "For other modules"): call these inside your own transaction. Each movement is
keyed by the order (and reason), so a retried cut-off or refund never moves money twice.
"""

import copy
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import service
from gharbasket.modules.billing.events import WALLET_TOPPED_UP
from gharbasket.modules.billing.models import JournalEntry, Payment, PaymentWebhook, Posting
from gharbasket.modules.billing.wallet import (
    InsufficientBalance,
    balance,
    debit_for_order,
    refund_to_wallet,
)

__all__ = [
    "WALLET_TOPPED_UP",
    "InsufficientBalance",
    "anonymise_for_user",
    "balance",
    "debit_for_order",
    "export_for_user",
    "refund_to_wallet",
]


# Personal fields Razorpay puts on a payment entity (M1 §3.5).
REDACTED = ("contact", "email", "vpa")


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


async def anonymise_for_user(
    session: AsyncSession, user_id: uuid.UUID, household_id: uuid.UUID | None
) -> dict[str, int]:
    """M1's purge (M1 §3.5, order 9). The ledger is append-only and top-ups stay for
    reconciliation; in the stored Razorpay webhooks of the user's payments, `contact`, `email`
    and `vpa` become "redacted"."""
    orders = select(Payment.provider_order_id).where(Payment.user_id == user_id)
    hooks = await session.scalars(
        select(PaymentWebhook).where(
            PaymentWebhook.payload["payment"]["entity"]["order_id"].astext.in_(orders)
        )
    )
    redacted = 0
    for hook in hooks.all():
        payload = copy.deepcopy(hook.payload)
        entity = payload["payment"]["entity"]
        found = [field for field in REDACTED if entity.get(field) not in (None, "redacted")]
        if found:
            entity.update(dict.fromkeys(found, "redacted"))
            hook.payload, redacted = payload, redacted + 1  # a new dict: JSONB sees the change
    return {"payment_webhooks": redacted}
