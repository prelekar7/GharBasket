"""A partner's earnings by calendar month (spec §3.5, for M8's income certificate §3.7).

Read from M5's own tables only. A month is an IST calendar month, as everywhere in M5:
- **Earned** is the `earning` entries posted to the partners' `partner_payable` accounts, by the
  month of the work (`earned_at`, as `partner_week` counts a week). `reverse_earning` is not
  built (§3.5), so no earning is reduced yet.
- **Paid out** is the money that left those accounts for a bank account: the `payout` postings
  less the `payout_reversed` ones (a failed transfer comes back and cancels), by when they were
  posted.
- **Owed** is the accounts' balance at the end of the last month, or at `until` when that month
  is still running: the `closing_paise` of the earnings statement of that month (statements.py).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing.earnings import BasisTotal, _earned_at
from gharbasket.modules.billing.models import JournalEntry, LedgerAccount, Posting
from gharbasket.modules.billing.statements import parse_period

# payouts.py's SETTLEMENT_KINDS; not imported, since payouts.py reaches M8's facade at import time.
SETTLEMENT_KINDS = ("payout", "payout_reversed")


@dataclass(frozen=True)
class MonthTotal:
    month: str  # "2026-10"
    by_basis: list[BasisTotal]
    earned_paise: int
    paid_out_paise: int


@dataclass(frozen=True)
class PartnerMonths:
    months: list[MonthTotal]  # every month from first to last, zeros included
    owed_paise: int


def _ist_month(moment: Any) -> Any:
    return func.to_char(func.timezone("Asia/Kolkata", moment), "YYYY-MM")


def _labels(first: str, last: str) -> list[str]:
    year, month = int(first[:4]), int(first[5:])
    labels = []
    while f"{year}-{month:02d}" <= last:
        labels.append(f"{year}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return labels


async def partner_months(
    session: AsyncSession,
    partner_ids: list[uuid.UUID],
    first: str,
    last: str,
    *,
    until: datetime,
) -> PartnerMonths:
    """Earned, paid out and owed for these partner records over the months `first` to `last`
    (`YYYY-MM`, IST, both included; neither after the month of `until`, nor `first` after `last`).
    `until` is now: the instant the balance is read at while `last` is the current month."""
    start, end = parse_period(first, until).start, parse_period(last, until).end
    labels = _labels(first, last)
    if not labels:
        raise ValueError("first is after last")
    on_payable = (
        LedgerAccount.code == "partner_payable",
        LedgerAccount.owner_type == "partner",
        LedgerAccount.owner_id.in_(partner_ids),
    )
    earned_at = _earned_at()
    # Labelled, so GROUP BY names the column instead of repeating an expression with bind values.
    month = _ist_month(earned_at).label("month")
    basis = JournalEntry.details["basis"].astext.label("basis")
    earned = await session.execute(
        select(
            month,
            LedgerAccount.owner_id,
            basis,
            func.sum(cast(JournalEntry.details["units"].astext, Integer)),
            func.sum(-Posting.amount),
        )
        .join(Posting, Posting.entry_id == JournalEntry.id)
        .join(LedgerAccount, LedgerAccount.id == Posting.account_id)
        .where(*on_payable, JournalEntry.kind == "earning", earned_at >= start, earned_at < end)
        .group_by(month, LedgerAccount.owner_id, basis)
        .order_by(LedgerAccount.owner_id, basis)
    )
    by_basis: dict[str, list[BasisTotal]] = {label: [] for label in labels}
    for label, partner_id, name, units, amount in earned.all():
        assert partner_id is not None  # a partner_payable account has its owner
        by_basis[label].append(BasisTotal(partner_id, name, int(units), int(amount)))
    posted = _ist_month(JournalEntry.created_at).label("month")
    settled = await session.execute(
        select(posted, func.sum(Posting.amount))
        .join(Posting, Posting.entry_id == JournalEntry.id)
        .join(LedgerAccount, LedgerAccount.id == Posting.account_id)
        .where(
            *on_payable,
            JournalEntry.kind.in_(SETTLEMENT_KINDS),
            JournalEntry.created_at >= start,
            JournalEntry.created_at < end,
        )
        .group_by(posted)
    )
    paid_out = {label: int(amount) for label, amount in settled.all()}
    balance = await session.execute(
        select(func.coalesce(func.sum(Posting.amount), 0))
        .join(JournalEntry, JournalEntry.id == Posting.entry_id)
        .join(LedgerAccount, LedgerAccount.id == Posting.account_id)
        .where(*on_payable, JournalEntry.created_at < min(end, until))
    )
    return PartnerMonths(
        months=[
            MonthTotal(
                label,
                by_basis[label],
                sum(line.amount_paise for line in by_basis[label]),
                paid_out.get(label, 0),
            )
            for label in labels
        ],
        owed_paise=-int(balance.scalar_one()),
    )
