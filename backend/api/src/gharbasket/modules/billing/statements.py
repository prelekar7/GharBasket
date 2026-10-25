"""Statements (spec §3.2): a month of the wallet, read from the ledger only; a partner's
earnings statement (§3.5) is the same month over her `partner_payable` accounts.

A month is a calendar month in IST and an entry falls in it by its `at`. The opening balance is
one sum over the postings before the month; the month's entries then carry the running balance
forward in posting order (entry ids are uuidv7), so opening + credits + debits = closing.
"""

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import service
from gharbasket.modules.billing.models import JournalEntry, LedgerAccount, Posting
from gharbasket.modules.billing.wallet import IST, WalletEntry
from gharbasket.platform.errors import ProblemError

# Years 1000 to 2999: an earlier one cannot be converted to UTC, a later one is in the future.
PERIOD = re.compile(r"^([12]\d{3})-(0[1-9]|1[0-2])$")


def period_invalid() -> ProblemError:
    return ProblemError(
        422,
        "billing.period_invalid",
        "The period is a month as YYYY-MM, no later than the current month (IST).",
    )


@dataclass(frozen=True)
class Month:
    """A calendar month in IST, as the UTC instants [start, end)."""

    period: str
    start: datetime
    end: datetime


def parse_period(period: str, now: datetime | None = None) -> Month:
    match = PERIOD.match(period)
    if match is None:
        raise period_invalid()
    year, month = int(match[1]), int(match[2])
    today = (now or datetime.now(UTC)).astimezone(IST)
    if (year, month) > (today.year, today.month):
        raise period_invalid()
    following = (year + 1, 1) if month == 12 else (year, month + 1)
    return Month(
        period=period,
        start=datetime(year, month, 1, tzinfo=IST).astimezone(UTC),
        end=datetime(*following, 1, tzinfo=IST).astimezone(UTC),
    )


@dataclass(frozen=True)
class StatementLine:
    row: WalletEntry
    balance_after_paise: int


@dataclass(frozen=True)
class Statement:
    month: Month
    opening_paise: int
    credits_paise: int
    debits_paise: int  # signed: zero or negative
    closing_paise: int
    lines: list[StatementLine]  # newest first


async def statement(session: AsyncSession, user_id: uuid.UUID, month: Month) -> Statement:
    wallet = await service.find_account(session, "wallet", "user", user_id)
    return await _statement(session, [] if wallet is None else [wallet], month)


async def earnings_statement(
    session: AsyncSession, partner_ids: list[uuid.UUID], month: Month
) -> Statement:
    """The same month over what a partner is owed (§3.5): her `partner_payable` accounts, one per
    partner record (kind), as one running balance. Earnings come in, payouts go out, and a
    payout the provider failed comes back."""
    accounts: list[LedgerAccount] = []
    for partner_id in partner_ids:
        found = await service.find_account(session, "partner_payable", "partner", partner_id)
        if found is not None:
            accounts.append(found)
    return await _statement(session, accounts, month)


async def _statement(
    session: AsyncSession, accounts: list[LedgerAccount], month: Month
) -> Statement:
    """Liability accounts only (a wallet, partner_payable): a credit (-) is money in."""
    if not accounts:  # no account yet: nothing ever posted
        return Statement(month, 0, 0, 0, 0, [])
    ids = [account.id for account in accounts]
    opening = await _balance_before(session, ids, month.start)
    rows = (
        await session.execute(
            select(JournalEntry, Posting.amount)
            .join(Posting, Posting.entry_id == JournalEntry.id)
            .where(
                Posting.account_id.in_(ids),
                JournalEntry.created_at >= month.start,
                JournalEntry.created_at < month.end,
            )
            .order_by(JournalEntry.id)
        )
    ).all()
    lines: list[StatementLine] = []
    running = opening
    for entry, amount in rows:
        row = WalletEntry(entry, -amount)  # a liability: a credit (-) is money into the account
        running += row.amount_paise
        lines.append(StatementLine(row, running))
    amounts = [line.row.amount_paise for line in lines]
    return Statement(
        month=month,
        opening_paise=opening,
        credits_paise=sum(a for a in amounts if a > 0),
        debits_paise=sum(a for a in amounts if a < 0),
        closing_paise=running,
        lines=lines[::-1],
    )


async def _balance_before(session: AsyncSession, ids: list[uuid.UUID], at: datetime) -> int:
    """The accounts' balance at `at`: one sum in Postgres, not a walk over the history."""
    total = (
        await session.execute(
            select(func.coalesce(func.sum(Posting.amount), 0))
            .join(JournalEntry, JournalEntry.id == Posting.entry_id)
            .where(Posting.account_id.in_(ids), JournalEntry.created_at < at)
        )
    ).scalar_one()
    return -int(total)
