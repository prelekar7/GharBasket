"""The ledger primitives of M5 (spec §4.1): accounts, posting an entry exactly once, balances.

Postings are signed (debit +, credit -). The balance of an asset or expense account is the sum of
its postings; of a liability or revenue account, minus that sum, so a wallet's balance is what
we owe the customer and is positive.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing.models import JournalEntry, LedgerAccount, Posting
from gharbasket.platform.errors import ProblemError

CREDIT_NORMAL = ("liability", "revenue")


def entry_conflict(key: str) -> ProblemError:
    return ProblemError(
        409,
        "billing.entry_conflict",
        "This money movement was already recorded with different amounts.",
        extra={"idempotency_key": key},
    )


async def platform_account(
    session: AsyncSession, code: str, currency: str = "INR"
) -> LedgerAccount:
    """One of the accounts the migrations create (razorpay_clearing, unearned_revenue, …, and
    kindness_expense in KINDNESS_PTS)."""
    return (
        await session.execute(
            select(LedgerAccount).where(
                LedgerAccount.code == code,
                LedgerAccount.owner_type == "platform",
                LedgerAccount.currency == currency,
            )
        )
    ).scalar_one()


async def owned_account(
    session: AsyncSession,
    code: str,
    owner_type: str,
    owner_id: uuid.UUID,
    *,
    kind: str,
    currency: str = "INR",
    lock: bool = False,
) -> LedgerAccount:
    """A user's or partner's account, created on first use. `lock` holds its row until the end
    of the transaction, so balance checks and postings on it happen one at a time."""
    await session.execute(
        insert(LedgerAccount)
        .values(code=code, owner_type=owner_type, owner_id=owner_id, currency=currency, kind=kind)
        .on_conflict_do_nothing()
    )
    stmt = select(LedgerAccount).where(
        LedgerAccount.code == code,
        LedgerAccount.owner_type == owner_type,
        LedgerAccount.owner_id == owner_id,
        LedgerAccount.currency == currency,
    )
    if lock:
        stmt = stmt.with_for_update()
    return (await session.execute(stmt)).scalar_one()


async def find_account(
    session: AsyncSession, code: str, owner_type: str, owner_id: uuid.UUID, currency: str = "INR"
) -> LedgerAccount | None:
    """Without creating it: reads must not write."""
    return (
        await session.execute(
            select(LedgerAccount).where(
                LedgerAccount.code == code,
                LedgerAccount.owner_type == owner_type,
                LedgerAccount.owner_id == owner_id,
                LedgerAccount.currency == currency,
            )
        )
    ).scalar_one_or_none()


async def balance(session: AsyncSession, account: LedgerAccount) -> int:
    total = (
        await session.execute(
            select(func.coalesce(func.sum(Posting.amount), 0)).where(
                Posting.account_id == account.id
            )
        )
    ).scalar_one()
    return -int(total) if account.kind in CREDIT_NORMAL else int(total)


@dataclass(frozen=True)
class Leg:
    account: LedgerAccount
    amount: int  # debit +, credit -


async def post(
    session: AsyncSession,
    *,
    kind: str,
    key: str,
    legs: list[Leg],
    ref_type: str | None = None,
    ref_id: uuid.UUID | None = None,
    details: dict[str, Any] | None = None,
) -> tuple[JournalEntry, bool]:
    """Record one journal entry for `key`, or return the one already recorded: (entry, created).

    The same key with different legs is a 409, never a second entry.
    """
    sums: dict[str, int] = defaultdict(int)
    for leg in legs:
        if leg.amount == 0:
            raise ValueError("a posting of zero")
        sums[leg.account.currency] += leg.amount
    if len(legs) < 2 or any(sums.values()):
        raise ValueError(f"journal entry {key!r} does not sum to zero per currency")
    if len({leg.account.id for leg in legs}) != len(legs):
        raise ValueError("one posting per account")

    existing = await find_entry(session, key)
    if existing is not None:
        return await _same(session, existing, kind, legs), False
    entry = JournalEntry(
        kind=kind, ref_type=ref_type, ref_id=ref_id, details=details, idempotency_key=key
    )
    try:
        # A savepoint: if the same cause is being posted at this very moment, the loser of the
        # race gets the winner's entry instead of an error.
        async with session.begin_nested():
            session.add(entry)
            await session.flush()
            session.add_all(
                Posting(entry_id=entry.id, account_id=leg.account.id, amount=leg.amount)
                for leg in legs
            )
            await session.flush()
    except IntegrityError:
        existing = await find_entry(session, key)
        if existing is None:
            raise
        return await _same(session, existing, kind, legs), False
    return entry, True


async def find_entry(session: AsyncSession, key: str) -> JournalEntry | None:
    return (
        await session.execute(select(JournalEntry).where(JournalEntry.idempotency_key == key))
    ).scalar_one_or_none()


async def _same(
    session: AsyncSession, entry: JournalEntry, kind: str, legs: list[Leg]
) -> JournalEntry:
    rows = await session.execute(
        select(Posting.account_id, Posting.amount).where(Posting.entry_id == entry.id)
    )
    recorded = {account_id: amount for account_id, amount in rows.all()}
    wanted = {leg.account.id: leg.amount for leg in legs}
    if entry.kind != kind or recorded != wanted:
        raise entry_conflict(entry.idempotency_key)
    return entry
