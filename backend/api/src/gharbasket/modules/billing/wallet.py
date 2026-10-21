"""The customer wallet (spec §3.1, ADR-0004): a `wallet` liability account per user.

Every change locks the wallet's account row first, so two movements at the same moment are
checked one after the other: the balance never goes below zero or above the ₹10,000 cap, counting
the refunds to source still in flight (spec §3.3), so a failed one always fits back. Each
movement is keyed by its cause, so retrying it (a webhook delivered twice, a cut-off job rerun)
never moves money twice.
"""

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import service
from gharbasket.modules.billing.models import (
    IN_FLIGHT,
    JournalEntry,
    LedgerAccount,
    Payment,
    Posting,
    Refund,
    RefundPart,
)
from gharbasket.modules.billing.service import Leg
from gharbasket.platform.errors import ProblemError

CAP_PAISE = 1_000_000  # ₹10,000
MONTHLY_TOPUP_PAISE = 2_000_000  # ₹20,000 a calendar month (IST)
IST = ZoneInfo("Asia/Kolkata")
REASON = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


class InsufficientBalance(ProblemError):
    """Not enough in the wallet for a box: M4 turns this into PAYMENT_FAILED."""

    def __init__(self, balance_paise: int, needed_paise: int) -> None:
        super().__init__(
            409,
            "billing.insufficient_balance",
            "Not enough money in the wallet.",
            extra={"balance_paise": balance_paise, "needed_paise": needed_paise},
        )


def wallet_limit(room_paise: int) -> ProblemError:
    return ProblemError(
        422,
        "billing.wallet_limit",
        "The wallet can hold at most ₹10,000.",
        extra={"room_paise": room_paise},
    )


async def _account(session: AsyncSession, user_id: uuid.UUID) -> LedgerAccount:
    return await service.owned_account(
        session, "wallet", "user", user_id, kind="liability", lock=True
    )


def _positive(amount_paise: int) -> None:
    if amount_paise <= 0:
        raise ValueError("amounts are positive paise")


async def credit_topup(
    session: AsyncSession,
    user_id: uuid.UUID,
    amount_paise: int,
    *,
    payment_ref: str,
    topup_id: uuid.UUID,
) -> tuple[JournalEntry, bool]:
    """A captured payment into the wallet (razorpay_clearing → wallet), once per payment."""
    _positive(amount_paise)
    wallet = await _account(session, user_id)
    clearing = await service.platform_account(session, "razorpay_clearing")
    key = f"topup:{payment_ref}"
    if await service.find_entry(session, key) is None:
        current = await service.balance(session, wallet) + await in_flight_paise(session, user_id)
        if current + amount_paise > CAP_PAISE:
            raise wallet_limit(max(0, CAP_PAISE - current))
    return await service.post(
        session,
        kind="topup",
        key=key,
        legs=[Leg(clearing, amount_paise), Leg(wallet, -amount_paise)],
        ref_type="topup",
        ref_id=topup_id,
    )


async def debit_for_order(
    session: AsyncSession,
    user_id: uuid.UUID,
    order_id: uuid.UUID,
    amount_paise: int,
    delivery_date: date,
) -> JournalEntry:
    """A box paid at cut-off (wallet → unearned_revenue), once per order."""
    _positive(amount_paise)
    wallet = await _account(session, user_id)
    unearned = await service.platform_account(session, "unearned_revenue")
    key = f"box:{order_id}"
    if await service.find_entry(session, key) is None:
        current = await service.balance(session, wallet)
        if current < amount_paise:
            raise InsufficientBalance(current, amount_paise)
    entry, _ = await service.post(
        session,
        kind="box_debit",
        key=key,
        legs=[Leg(wallet, amount_paise), Leg(unearned, -amount_paise)],
        ref_type="order",
        ref_id=order_id,
        details={"delivery_date": delivery_date.isoformat()},
    )
    return entry


async def refund_to_wallet(
    session: AsyncSession,
    user_id: uuid.UUID,
    order_id: uuid.UUID,
    amount_paise: int,
    reason: str,
) -> JournalEntry:
    """Money back for an order (unearned_revenue → wallet), once per order and reason.

    A refund that would take the wallet over the cap goes to the payment source in the same call
    (spec §3.3, "Order refunds when the wallet is full"): the `refund` entry, a Refund and its
    `refund_to_source` parts. It is all or nothing: when the top-ups cannot take the whole amount,
    `billing.wallet_limit` is raised and nothing is posted.
    """
    _positive(amount_paise)
    if not REASON.match(reason):
        raise ValueError("reason is a short code such as 'shortage' or 'undelivered'")
    wallet = await _account(session, user_id)
    unearned = await service.platform_account(session, "unearned_revenue")
    key = f"refund:{order_id}:{reason}"
    details: dict[str, str] = {"reason": reason}
    debit = await service.find_entry(session, f"box:{order_id}")
    if debit is not None and debit.details and "delivery_date" in debit.details:
        details["delivery_date"] = debit.details["delivery_date"]  # shown on the refund too
    if await service.find_entry(session, key) is None:
        current = await service.balance(session, wallet) + await in_flight_paise(session, user_id)
        if current + amount_paise > CAP_PAISE:
            # refunds.py imports this module, so it is imported here, where it is used.
            from gharbasket.modules.billing import refunds

            return await refunds.refund_order_to_source(
                session, wallet, user_id, order_id, amount_paise, reason, details, current
            )
    entry, _ = await service.post(
        session,
        kind="refund",
        key=key,
        legs=[Leg(unearned, amount_paise), Leg(wallet, -amount_paise)],
        ref_type="order",
        ref_id=order_id,
        details=details,
    )
    return entry


# --- reading -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class WalletEntry:
    entry: JournalEntry
    amount_paise: int  # into the wallet +, out of it -


@dataclass(frozen=True)
class Summary:
    balance_paise: int
    topup_room_paise: int
    entries: list[WalletEntry]


async def balance(session: AsyncSession, user_id: uuid.UUID) -> int:
    wallet = await service.find_account(session, "wallet", "user", user_id)
    return 0 if wallet is None else await service.balance(session, wallet)


async def summary(session: AsyncSession, user_id: uuid.UUID, latest: int = 20) -> Summary:
    wallet = await service.find_account(session, "wallet", "user", user_id)
    if wallet is None:
        held = await held_paise(session, user_id)  # a first top-up may still be unpaid
        return Summary(0, max(0, min(CAP_PAISE, MONTHLY_TOPUP_PAISE) - held), [])
    current = await service.balance(session, wallet)
    held = await held_paise(session, user_id)
    room = min(
        CAP_PAISE - current - held - await in_flight_paise(session, user_id),
        MONTHLY_TOPUP_PAISE - await topped_up_this_month(session, wallet) - held,
    )
    entries, _ = await _entries(session, wallet, None, latest)
    return Summary(current, max(0, room), entries)


async def entries(
    session: AsyncSession, user_id: uuid.UUID, cursor: str | None, limit: int
) -> tuple[list[WalletEntry], str | None]:
    wallet = await service.find_account(session, "wallet", "user", user_id)
    if wallet is None:
        return [], None
    return await _entries(session, wallet, cursor, limit)


async def _entries(
    session: AsyncSession, wallet: LedgerAccount, cursor: str | None, limit: int
) -> tuple[list[WalletEntry], str | None]:
    rows, next_cursor = await service.account_entries(session, wallet, cursor, limit)
    return [WalletEntry(entry, amount) for entry, amount in rows], next_cursor


async def topped_up_this_month(session: AsyncSession, wallet: LedgerAccount) -> int:
    """Top-ups credited since the 1st of this month (IST)."""
    now = datetime.now(IST)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
    total = (
        await session.execute(
            select(func.coalesce(func.sum(-Posting.amount), 0))
            .join(JournalEntry, JournalEntry.id == Posting.entry_id)
            .where(
                Posting.account_id == wallet.id,
                JournalEntry.kind == "topup",
                JournalEntry.created_at >= month_start,
            )
        )
    ).scalar_one()
    return int(total)


async def held_paise(session: AsyncSession, user_id: uuid.UUID) -> int:
    """Unpaid top-ups that have not expired yet: their amounts stay free under the limits."""
    total = (
        await session.execute(
            select(func.coalesce(func.sum(Payment.amount_paise), 0)).where(
                Payment.user_id == user_id,
                Payment.status == "created",
                Payment.expires_at > func.now(),
            )
        )
    ).scalar_one()
    return int(total)


async def in_flight_paise(session: AsyncSession, user_id: uuid.UUID) -> int:
    """Refund-to-source parts not settled yet: they hold their room under the cap (spec §3.3)."""
    total = (
        await session.execute(
            select(func.coalesce(func.sum(RefundPart.amount_paise), 0))
            .join(Refund, Refund.id == RefundPart.refund_id)
            .where(Refund.user_id == user_id, RefundPart.status.in_(IN_FLIGHT))
        )
    ).scalar_one()
    return int(total)
