"""The customer wallet (spec §3.1, ADR-0004): a `wallet` liability account per user.

Every change locks the wallet's account row first, so two movements at the same moment are
checked one after the other: the balance never goes below zero or above the ₹10,000 cap. Each
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
from gharbasket.modules.billing.models import JournalEntry, LedgerAccount, Posting
from gharbasket.modules.billing.service import Leg
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.pagination import decode_cursor, encode_cursor

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
        current = await service.balance(session, wallet)
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

    A refund that would take the wallet over the cap raises `billing.wallet_limit`: the caller
    refunds to the payment source instead (pilot phase).
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
        current = await service.balance(session, wallet)
        if current + amount_paise > CAP_PAISE:
            raise wallet_limit(max(0, CAP_PAISE - current))
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
        return Summary(0, min(CAP_PAISE, MONTHLY_TOPUP_PAISE), [])
    current = await service.balance(session, wallet)
    room = min(
        CAP_PAISE - current, MONTHLY_TOPUP_PAISE - await _topped_up_this_month(session, wallet)
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
    """Newest first. Entry ids are uuidv7 (time-ordered), so the cursor is the last id seen."""
    stmt = (
        select(JournalEntry, Posting.amount)
        .join(Posting, Posting.entry_id == JournalEntry.id)
        .where(Posting.account_id == wallet.id)
    )
    if cursor is not None:
        try:
            before = uuid.UUID(str(decode_cursor(cursor).get("before")))
        except ValueError as exc:
            raise ProblemError(422, "request.invalid_cursor", "The cursor is not valid.") from exc
        stmt = stmt.where(JournalEntry.id < before)
    rows = (await session.execute(stmt.order_by(JournalEntry.id.desc()).limit(limit + 1))).all()
    found = [WalletEntry(entry, -amount) for entry, amount in rows[:limit]]
    next_cursor = None
    if len(rows) > limit:
        next_cursor = encode_cursor({"before": str(found[-1].entry.id)})
    return found, next_cursor


async def _topped_up_this_month(session: AsyncSession, wallet: LedgerAccount) -> int:
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
