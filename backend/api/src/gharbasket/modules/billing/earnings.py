"""Partner earnings in the ledger (spec §3.5): M8 decides what a piece of work earns, M5 records it.

Each earning is one entry, `fulfilment_expense` to `partner_payable:<partner>`, keyed by partner,
basis and order, so a redelivered event never pays twice. The basis, the units and the time the
work was done (`earned_at`) are kept in the entry's `details`, and the views below read them back:
the week of one or more partner records with that week's payouts (payouts.py), and the rolling
total M8's fair assignment needs.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import DateTime, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import service
from gharbasket.modules.billing.models import JournalEntry, LedgerAccount, Payout, Posting
from gharbasket.modules.billing.schemas import PayoutOut, PayoutPeriod
from gharbasket.modules.billing.service import Leg
from gharbasket.modules.billing.wallet import IST

# The bases of M8's rate card (M8 §3.6): a portion, a bag, a dispatched order, a packed order.
BASES = ("tiffin_portion", "bag_delivered", "dispatched_order", "packed_order")


@dataclass(frozen=True)
class BasisTotal:
    partner_id: uuid.UUID
    basis: str
    units: int
    amount_paise: int


@dataclass(frozen=True)
class DayTotal:
    day: date
    amount_paise: int


@dataclass(frozen=True)
class EarningEntry:
    id: uuid.UUID
    partner_id: uuid.UUID
    basis: str
    units: int
    amount_paise: int
    ref_type: str | None
    ref_id: uuid.UUID | None
    at: datetime  # when the work was done


@dataclass(frozen=True)
class PartnerWeek:
    start: date  # Monday
    end: date  # Sunday
    total_paise: int
    by_basis: list[BasisTotal]
    days: list[DayTotal]  # seven, zeros included
    entries: list[EarningEntry]  # newest first
    payouts: list[PayoutOut]  # that week's payouts of these partners, by partner


async def accrue_earning(
    session: AsyncSession,
    *,
    partner_id: uuid.UUID,
    basis: str,
    ref_type: str,
    ref_id: uuid.UUID,
    units: int,
    amount_paise: int,
    earned_at: datetime,
    occurrence: uuid.UUID | None = None,
) -> uuid.UUID:
    """Owe the partner `amount_paise` for `units` of work on an order, once per (partner, basis,
    order) or, when work on the order can repeat (a box dispatched again for its free
    redelivery), once per `occurrence` (M4's timeline row of that trip); the entry's id (the same
    one when it is replayed). Positive amounts only: a rate of 0 earns nothing and is not
    recorded. The same key with other amounts is `billing.entry_conflict`."""
    if basis not in BASES or ref_type != "order":
        raise ValueError("a basis of the rate card, for an order")
    if units <= 0 or amount_paise <= 0:
        raise ValueError("units and amounts are positive")
    payable = await service.owned_account(
        session, "partner_payable", "partner", partner_id, kind="liability"
    )
    expense = await service.platform_account(session, "fulfilment_expense")
    entry, _ = await service.post(
        session,
        kind="earning",
        key=f"earning:{partner_id}:{basis}:{ref_id}"
        + ("" if occurrence is None else f":{occurrence}"),
        legs=[Leg(expense, amount_paise), Leg(payable, -amount_paise)],
        ref_type=ref_type,
        ref_id=ref_id,
        details={
            "partner_id": str(partner_id),
            "basis": basis,
            "units": units,
            **({} if occurrence is None else {"occurrence": str(occurrence)}),
            "earned_at": earned_at.astimezone(UTC).isoformat(),
        },
    )
    return entry.id


def week_bounds(week: str) -> tuple[date, date, datetime, datetime]:
    """Monday, Sunday and the UTC instants [start, end) of an ISO week such as `2027-W02` (IST)."""
    year, _, number = week.partition("-W")
    monday = date.fromisocalendar(int(year), int(number), 1)
    start = datetime(monday.year, monday.month, monday.day, tzinfo=IST)
    return (
        monday,
        monday + timedelta(days=6),
        start.astimezone(UTC),
        (start + timedelta(days=7)).astimezone(UTC),
    )


def _earned_at() -> Any:
    return cast(JournalEntry.details["earned_at"].astext, DateTime(timezone=True))


async def partner_week(
    session: AsyncSession, partner_ids: list[uuid.UUID], week: str
) -> PartnerWeek:
    """What the partner records earned in an ISO week (Monday to Sunday, IST), by the time the
    work was done: totals per partner and basis, per day, and every entry."""
    monday, sunday, start, end = week_bounds(week)
    earned = _earned_at()
    rows = (
        await session.execute(
            select(JournalEntry, Posting.amount, earned)
            .join(Posting, Posting.entry_id == JournalEntry.id)
            .join(LedgerAccount, LedgerAccount.id == Posting.account_id)
            .where(
                LedgerAccount.code == "partner_payable",
                LedgerAccount.owner_type == "partner",
                LedgerAccount.owner_id.in_(partner_ids),
                JournalEntry.kind == "earning",
                earned >= start,
                earned < end,
            )
            .order_by(earned.desc(), JournalEntry.id.desc())
        )
    ).all()
    entries = [
        EarningEntry(
            id=entry.id,
            partner_id=uuid.UUID((entry.details or {})["partner_id"]),
            basis=(entry.details or {})["basis"],
            units=int((entry.details or {})["units"]),
            amount_paise=-int(amount),
            ref_type=entry.ref_type,
            ref_id=entry.ref_id,
            at=at,
        )
        for entry, amount, at in rows
    ]
    totals: dict[tuple[uuid.UUID, str], list[int]] = {}
    per_day = {monday + timedelta(days=n): 0 for n in range(7)}
    for found in entries:
        pair = totals.setdefault((found.partner_id, found.basis), [0, 0])
        pair[0], pair[1] = pair[0] + found.units, pair[1] + found.amount_paise
        per_day[found.at.astimezone(IST).date()] += found.amount_paise
    return PartnerWeek(
        start=monday,
        end=sunday,
        total_paise=sum(e.amount_paise for e in entries),
        by_basis=[BasisTotal(p, b, u, a) for (p, b), (u, a) in sorted(totals.items(), key=str)],
        days=[DayTotal(day, amount) for day, amount in per_day.items()],
        entries=entries,
        payouts=await week_payouts(session, partner_ids, week),
    )


def payout_out(payout: Payout) -> PayoutOut:
    return PayoutOut(
        id=payout.id,
        partner_id=payout.partner_id,
        hub_id=payout.hub_id,
        week=payout.week,
        period=PayoutPeriod.model_validate({"from": payout.period_from, "to": payout.period_to}),
        amount_paise=payout.amount_paise,
        status=payout.status,  # pyright: ignore[reportArgumentType]
        hold_reason=payout.hold_reason,  # pyright: ignore[reportArgumentType]
        provider=payout.provider,  # pyright: ignore[reportArgumentType]
        provider_ref=payout.provider_ref,
        created_at=payout.created_at,
        processed_at=payout.processed_at,
    )


async def week_payouts(
    session: AsyncSession, partner_ids: list[uuid.UUID], week: str
) -> list[PayoutOut]:
    """The payout rows of one ISO week for these partner records (at most one each)."""
    if not partner_ids:
        return []
    rows = await session.execute(
        select(Payout)
        .where(Payout.partner_id.in_(partner_ids), Payout.week == week)
        .order_by(Payout.partner_id)
    )
    return [payout_out(payout) for payout in rows.scalars()]


async def partner_rolling_earnings(
    session: AsyncSession, partner_ids: list[uuid.UUID], basis: str, since: datetime
) -> dict[uuid.UUID, int]:
    """Each partner's earnings on one basis from `since` on (zero for those with none), for the
    fair assignment's `earnings_paise`."""
    earned = _earned_at()
    rows = await session.execute(
        select(LedgerAccount.owner_id, func.coalesce(func.sum(-Posting.amount), 0))
        .select_from(Posting)
        .join(JournalEntry, JournalEntry.id == Posting.entry_id)
        .join(LedgerAccount, LedgerAccount.id == Posting.account_id)
        .where(
            LedgerAccount.code == "partner_payable",
            LedgerAccount.owner_type == "partner",
            LedgerAccount.owner_id.in_(partner_ids),
            JournalEntry.kind == "earning",
            JournalEntry.details["basis"].astext == basis,
            earned >= since,
        )
        .group_by(LedgerAccount.owner_id)
    )
    found = {owner: int(total) for owner, total in rows.all()}
    return {partner_id: found.get(partner_id, 0) for partner_id in partner_ids}
