"""Kindness points (spec §3.6): a separate currency (`KINDNESS_PTS`), never money and never cash.

This is the credit side M9 needs for referral rewards (M9 §3.3) and, later, verified donations:
`points_earned` moves points from the platform's `kindness_expense` to the user's `kindness:<user>`
account, keyed by what earned them, and opens one lot that expires 365 days later (end of that
IST day). Balance, history, expiry and donating a meal (the rest of §3.6) follow with M9's
donations.
"""

import re
import uuid
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import events, service
from gharbasket.modules.billing.models import JournalEntry, PointsLot
from gharbasket.modules.billing.service import Leg
from gharbasket.platform.outbox import publish

CURRENCY = "KINDNESS_PTS"
EXPIRY_DAYS = 365
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
    account = await service.owned_account(
        session, "kindness", "user", user_id, kind="liability", currency=CURRENCY, lock=True
    )
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
