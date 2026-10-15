"""Kindness points (spec §3.6): a separate currency (`KINDNESS_PTS`), never money and never cash.

- `points_earned` (`credit_points`, for M9's referrals and verified donations) moves points from
  the platform's `kindness_expense` to the user's `kindness:<user>` account, keyed by what earned
  them, and opens one lot that expires 365 days later (end of that IST day).
- `points_donated` (`donate`) is the only way to use them: a meal for a child, taken from the
  lots that expire first.
- `points_expired` (`expire_lots`, the daily job) takes back what is left of a lot past expiry.

Every movement locks the user's `kindness` account row first, so a donation and the expiry job
are applied one after the other, and Σ lots.remaining always equals the ledger balance.
"""

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import events, service
from gharbasket.modules.billing.models import JournalEntry, LedgerAccount, PointsLot
from gharbasket.modules.billing.service import Leg
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.outbox import publish

CURRENCY = "KINDNESS_PTS"
EXPIRY_DAYS = 365
EXPIRING_WINDOW = timedelta(days=90)  # the balance lists the expiry dates in the next 90 days
EXPIRING_DATES = 3
GOAL_CODE = "child_meal"
IST = ZoneInfo("Asia/Kolkata")
SOURCE_TYPE = re.compile(r"^[a-z][a-z_]{0,39}$")
# The journal entry's ref_type for each source (spec §4.1).
REF_TYPES = {"donation": "donation", "referral": "referral", "referral_welcome": "referral"}


def expiry(earned_at: datetime) -> datetime:
    """The end of the IST day 365 days after the credit."""
    day = earned_at.astimezone(IST).date() + timedelta(days=EXPIRY_DAYS)
    return datetime.combine(day, time.max, tzinfo=IST).astimezone(UTC)


async def credit_points(
    session: AsyncSession,
    user_id: uuid.UUID,
    points: int,
    source_type: str,
    source_id: uuid.UUID,
) -> JournalEntry:
    """Credit `points` to the user once per (source_type, source_id), in the caller's
    transaction, and publish `billing.points_credited` the first time. A repeat returns the
    entry already posted; the same source with another amount is 409 `billing.entry_conflict`."""
    if points <= 0:
        raise ValueError("points are a positive whole number")
    if not SOURCE_TYPE.match(source_type):
        raise ValueError("source_type is a short code such as 'donation' or 'referral'")
    account = await _account(session, user_id)
    expense = await service.platform_account(session, "kindness_expense", CURRENCY)
    entry, created = await service.post(
        session,
        kind="points_earned",
        key=f"points:{source_type}:{source_id}",
        legs=[Leg(expense, points), Leg(account, -points)],
        ref_type=REF_TYPES.get(source_type),
        ref_id=source_id,
        details={"source_type": source_type},
    )
    if not created:
        return entry
    earned_at = datetime.now(UTC)
    expires_at = expiry(earned_at)
    await session.execute(
        insert(PointsLot)
        .values(
            user_id=user_id,
            points=points,
            remaining=points,
            earned_at=earned_at,
            expires_at=expires_at,
            source_type=source_type,
            source_id=source_id,
            entry_id=entry.id,
        )
        .on_conflict_do_nothing()
    )
    await publish(
        session,
        events.POINTS_CREDITED,
        {
            "user_id": str(user_id),
            "points": points,
            "source_type": source_type,
            "source_id": str(source_id),
            "expires_on": expires_at.astimezone(IST).date().isoformat(),
        },
    )
    return entry


async def _account(session: AsyncSession, user_id: uuid.UUID) -> LedgerAccount:
    return await service.owned_account(
        session, "kindness", "user", user_id, kind="liability", currency=CURRENCY, lock=True
    )


async def _live_lots(
    session: AsyncSession, user_id: uuid.UUID, at: datetime, *, lock: bool = False
) -> list[PointsLot]:
    """The lots with points left that have not expired at `at`, soonest expiry first."""
    stmt = (
        select(PointsLot)
        .where(PointsLot.user_id == user_id, PointsLot.remaining > 0, PointsLot.expires_at > at)
        .order_by(PointsLot.expires_at, PointsLot.id)
    )
    if lock:
        stmt = stmt.with_for_update()
    return list((await session.execute(stmt)).scalars())


# --- reading -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Expiring:
    points: int
    on: date  # IST


@dataclass(frozen=True)
class Balance:
    points: int
    expiring: list[Expiring]


async def balance(session: AsyncSession, user_id: uuid.UUID) -> Balance:
    """The points the user can donate now and the next expiry dates within 90 days. A lot past
    its expiry no longer counts, even in the half hour before the job takes it back."""
    now = datetime.now(UTC)
    lots = await _live_lots(session, user_id, now)
    by_day: dict[date, int] = {}
    for lot in lots:
        if lot.expires_at <= now + EXPIRING_WINDOW:
            day = lot.expires_at.astimezone(IST).date()
            by_day[day] = by_day.get(day, 0) + lot.remaining
    expiring = [Expiring(points, day) for day, points in sorted(by_day.items())]
    return Balance(sum(lot.remaining for lot in lots), expiring[:EXPIRING_DATES])


@dataclass(frozen=True)
class PointsEntry:
    entry: JournalEntry
    points: int  # into the account +, out of it -
    expires_on: date | None  # IST, for points_earned


async def entries(
    session: AsyncSession, user_id: uuid.UUID, cursor: str | None, limit: int
) -> tuple[list[PointsEntry], str | None]:
    account = await service.find_account(session, "kindness", "user", user_id, CURRENCY)
    if account is None:
        return [], None
    rows, next_cursor = await service.account_entries(session, account, cursor, limit)
    ids = [entry.id for entry, _ in rows if entry.kind == "points_earned"]
    expiry_of: dict[uuid.UUID, date] = {}
    if ids:
        lots = await session.execute(
            select(PointsLot.entry_id, PointsLot.expires_at).where(PointsLot.entry_id.in_(ids))
        )
        expiry_of = {entry_id: at.astimezone(IST).date() for entry_id, at in lots.all()}
    found = [PointsEntry(entry, points, expiry_of.get(entry.id)) for entry, points in rows]
    return found, next_cursor


# --- using and expiring ------------------------------------------------------------------------


def not_enough_points(points: int, needed: int) -> ProblemError:
    return ProblemError(
        409,
        "billing.not_enough_points",
        "Not enough Kindness points for this many meals.",
        extra={"points": points, "needed": needed},
    )


def _take(lots: list[PointsLot], points: int) -> None:
    """Take `points` from `lots`, which come soonest expiry first."""
    for lot in lots:
        taken = min(lot.remaining, points)
        lot.remaining -= taken
        points -= taken
        if points == 0:
            return


def donation_key(user_id: uuid.UUID, idempotency_key: str) -> str:
    key = f"points_donation:{user_id}:{idempotency_key}"
    if len(key) > 160:  # the ledger's key column; long client keys are hashed
        digest = hashlib.sha256(idempotency_key.encode()).hexdigest()
        key = f"points_donation:{user_id}:sha256:{digest}"
    return key


async def donate(
    session: AsyncSession, user_id: uuid.UUID, meals: int, goal: int, idempotency_key: str
) -> JournalEntry:
    """Give `meals` meals for `meals * goal` points, taken from the lots that expire first, and
    publish `billing.points_donated` (M9 counts the meals). Once per Idempotency-Key."""
    if not 1 <= meals <= 10 or goal <= 0:
        raise ValueError("1 to 10 meals, at a positive goal")
    needed = meals * goal
    account = await _account(session, user_id)
    expense = await service.platform_account(session, "kindness_expense", CURRENCY)
    key = donation_key(user_id, idempotency_key)
    lots: list[PointsLot] = []
    if await service.find_entry(session, key) is None:
        lots = await _live_lots(session, user_id, datetime.now(UTC), lock=True)
        available = sum(lot.remaining for lot in lots)
        if available < needed:
            raise not_enough_points(available, needed)
    entry, created = await service.post(
        session,
        kind="points_donated",
        key=key,
        legs=[Leg(account, needed), Leg(expense, -needed)],
        details={"meals": meals, "code": GOAL_CODE},
    )
    if not created:
        return entry
    _take(lots, needed)
    await session.flush()
    await publish(
        session,
        events.POINTS_DONATED,
        {
            "entry_id": str(entry.id),
            "user_id": str(user_id),
            "meals": meals,
            "points": needed,
            "code": GOAL_CODE,
        },
    )
    return entry


async def expire_lots(session: AsyncSession, user_id: uuid.UUID, at: datetime) -> tuple[int, int]:
    """Post `points_expired` for each of the user's lots past expiry at `at` with points left,
    oldest first, once per lot. Returns (lots, points)."""
    account = await _account(session, user_id)
    expense = await service.platform_account(session, "kindness_expense", CURRENCY)
    lots = (
        (
            await session.execute(
                select(PointsLot)
                .where(
                    PointsLot.user_id == user_id,
                    PointsLot.remaining > 0,
                    PointsLot.expires_at <= at,
                )
                .order_by(PointsLot.expires_at, PointsLot.id)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    count = total = 0
    for lot in lots:
        await service.post(
            session,
            kind="points_expired",
            key=f"points_expiry:{lot.id}",
            legs=[Leg(account, lot.remaining), Leg(expense, -lot.remaining)],
            ref_type=REF_TYPES.get(lot.source_type),
            ref_id=lot.source_id,
            details={"source_type": lot.source_type, "lot_id": str(lot.id)},
        )
        count, total = count + 1, total + lot.remaining
        lot.remaining = 0
    await session.flush()
    return count, total


async def users_with_expired_lots(
    session: AsyncSession, at: datetime, *, after: uuid.UUID | None, limit: int
) -> list[uuid.UUID]:
    stmt = (
        select(PointsLot.user_id)
        .where(PointsLot.remaining > 0, PointsLot.expires_at <= at)
        .group_by(PointsLot.user_id)
        .order_by(PointsLot.user_id)
        .limit(limit)
    )
    if after is not None:
        stmt = stmt.where(PointsLot.user_id > after)
    return list((await session.execute(stmt)).scalars())
