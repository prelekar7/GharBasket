"""Fact rows of M5 for M12's analytics refresh (M12 §3.1): partner earnings per ISO week and
Kindness points per day, read back from the ledger. No user, name or phone: earnings carry the
partner id only, and points are totals per day.

M5 stores no data source: M12 takes an earning's from its partner (`partners.api.partner_facts`).
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from sqlalchemy import Date, DateTime, Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing.earnings import week_bounds
from gharbasket.modules.billing.models import JournalEntry, LedgerAccount, Posting

IST_NAME = "Asia/Kolkata"


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


async def earning_facts(
    session: AsyncSession, *, week_from: str, week_to: str
) -> list[EarningFact]:
    """What each partner earned per basis in the ISO weeks `week_from` to `week_to` (inclusive,
    Monday to Sunday IST), by the time the work was done."""
    start = week_bounds(week_from)[2]
    end = week_bounds(week_to)[3]
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
