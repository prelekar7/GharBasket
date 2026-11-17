"""Fact rows of M5 for M12's analytics refresh (M12 §3.1): partner earnings per ISO week and
Kindness points per day, read back from the ledger. No user, name or phone: earnings carry the
partner id only, and points are totals per day.

M5 stores no data source: M12 takes an earning's from its partner (`partners.api.partner_facts`).

After the cut-over (spec §3.10) M12's money views count only entries after the `test_mode_closed`
entry: `cutover_at` says when it was posted, and `earning_facts(after_cutover=True)` leaves out the
earnings posted before it.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import service
from gharbasket.modules.billing.earnings import week_bounds
from gharbasket.modules.billing.models import JournalEntry, LedgerAccount, Posting

IST_NAME = "Asia/Kolkata"
# The closing entry's key, `cutover.KEY`; not imported from there: cutover.py imports M3's and M4's
# facades, and they import this one.
CLOSED_KEY = "test_mode_closed"


@dataclass(frozen=True)
class EarningFact:
    partner_id: uuid.UUID
    iso_week: str  # "2026-W43", by when the work was done (IST)
    basis: str  # tiffin_portion | bag_delivered | dispatched_order | packed_order
    units: int
    amount_paise: int


@dataclass(frozen=True)
class KindnessFact:
    on: date  # IST
    source_type: str | None  # donation | referral | referral_welcome; None for donated meals
    credited: int  # points
    donated: int
    expired: int
    meals: int  # donated meals


async def cutover_at(session: AsyncSession) -> datetime | None:
    """When the cut-over's `test_mode_closed` entry was posted (spec §3.10); None before it."""
    closed = await service.find_entry(session, CLOSED_KEY)
    return None if closed is None else closed.created_at


async def earning_facts(
    session: AsyncSession, *, week_from: str, week_to: str, after_cutover: bool = False
) -> list[EarningFact]:
    """What each partner earned per basis in the ISO weeks `week_from` to `week_to` (inclusive,
    Monday to Sunday IST), by the time the work was done. With `after_cutover`, only the entries
    posted after the `test_mode_closed` entry count, when it exists (spec §3.10): the earnings
    before it were test money, which that entry closed."""
    start = week_bounds(week_from)[2]
    end = week_bounds(week_to)[3]
    closed = await service.find_entry(session, CLOSED_KEY) if after_cutover else None
    earned = cast(JournalEntry.details["earned_at"].astext, DateTime(timezone=True))
    week = func.to_char(func.timezone(IST_NAME, earned), 'IYYY-"W"IW')
    basis = JournalEntry.details["basis"].astext
    rows = await session.execute(
        select(
            LedgerAccount.owner_id,
            week,
            basis,
            func.sum(cast(JournalEntry.details["units"].astext, Integer)),
            func.sum(-Posting.amount),
        )
        .select_from(Posting)
        .join(JournalEntry, JournalEntry.id == Posting.entry_id)
        .join(LedgerAccount, LedgerAccount.id == Posting.account_id)
        .where(
            LedgerAccount.code == "partner_payable",
            LedgerAccount.owner_type == "partner",
            JournalEntry.kind == "earning",
            earned >= start,
            earned < end,
            # uuidv7: after it, as the probe reads it (cutover.mixed)
            *([JournalEntry.id > closed.id] if closed is not None else []),
        )
        .group_by(LedgerAccount.owner_id, week, basis)
        .order_by(week, LedgerAccount.owner_id, basis)
    )
    return [
        EarningFact(partner_id, iso_week, basis_, int(units), int(amount))
        for partner_id, iso_week, basis_, units, amount in rows
        if partner_id is not None
    ]


KINDS = ("points_earned", "points_donated", "points_expired")


def _key(key: tuple[date, str | None]) -> tuple[date, str]:
    return key[0], key[1] or ""


async def kindness_facts(
    session: AsyncSession, *, date_from: date, date_to: date
) -> list[KindnessFact]:
    """Kindness points credited, donated and expired per IST day (inclusive window) and source,
    with the meals donated; oldest day first."""
    on = cast(func.timezone(IST_NAME, JournalEntry.created_at), Date)
    source = JournalEntry.details["source_type"].astext
    # Each points entry has one positive leg: its points.
    points = (
        select(Posting.entry_id, func.sum(Posting.amount).label("points"))
        .where(Posting.amount > 0)
        .group_by(Posting.entry_id)
        .subquery()
    )
    rows = await session.execute(
        select(
            on,
            source,
            JournalEntry.kind,
            func.sum(points.c.points),
            func.sum(func.coalesce(cast(JournalEntry.details["meals"].astext, Integer), 0)),
        )
        .join(points, points.c.entry_id == JournalEntry.id)
        .where(JournalEntry.kind.in_(KINDS), on >= date_from, on <= date_to)
        .group_by(on, source, JournalEntry.kind)
    )
    found: dict[tuple[date, str | None], list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    for day, source_type, kind, total, meals in rows:
        row = found[(day, None if kind == "points_donated" else source_type)]
        row[KINDS.index(kind)] += int(total)
        row[3] += int(meals)
    return [
        KindnessFact(day, source_type, *values)
        for (day, source_type), values in sorted(found.items(), key=lambda kv: _key(kv[0]))
    ]
