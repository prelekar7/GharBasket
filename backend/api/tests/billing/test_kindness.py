"""Kindness points credits (M5 §3.6): a separate currency, credited once per source, one lot each
with its expiry, and `billing.points_credited` once."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.billing import kindness, service
from gharbasket.modules.billing.models import LedgerAccount, PointsLot, Posting
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.models import OutboxEvent

pytestmark = [pytest.mark.anyio, pytest.mark.services]


async def _points(session: AsyncSession, user_id: uuid.UUID) -> int:
    account = await service.find_account(session, "kindness", "user", user_id, "KINDNESS_PTS")
    return 0 if account is None else await service.balance(session, account)


async def test_points_are_credited_once_per_source_with_one_lot_and_one_event(
    db_session: AsyncSession,
) -> None:
    user, referral = uuid.uuid4(), uuid.uuid4()

    first = await billing_api.credit_points(db_session, user, 250, "referral", referral)
    again = await billing_api.credit_points(db_session, user, 250, "referral", referral)
    other = await billing_api.credit_points(db_session, user, 250, "referral_welcome", referral)

    assert first.id == again.id != other.id
    assert (first.kind, first.ref_type, first.ref_id) == ("points_earned", "referral", referral)
    assert first.idempotency_key == f"points:referral:{referral}"
    assert await _points(db_session, user) == 500
    lots = (await db_session.execute(select(PointsLot).where(PointsLot.user_id == user))).scalars()
    assert sorted((lot.points, lot.remaining, lot.source_type) for lot in lots) == [
        (250, 250, "referral"),
        (250, 250, "referral_welcome"),
    ]
    events = (
        await db_session.execute(
            select(OutboxEvent).where(OutboxEvent.event_type == billing_api.POINTS_CREDITED)
        )
    ).scalars()
    payloads = [e.payload for e in events if e.payload["user_id"] == str(user)]
    assert len(payloads) == 2
    assert set(payloads[0]) == {"user_id", "points", "source_type", "source_id", "expires_on"}


async def test_points_never_touch_money(db_session: AsyncSession) -> None:
    user = uuid.uuid4()
    entry = await billing_api.credit_points(db_session, user, 120, "donation", uuid.uuid4())

    currencies = (
        await db_session.execute(
            select(func.count())
            .select_from(Posting)
            .where(Posting.entry_id == entry.id)
            .join(LedgerAccount, LedgerAccount.id == Posting.account_id)
            .where(LedgerAccount.currency == "INR")
        )
    ).scalar_one()
    assert currencies == 0
    assert await billing_api.balance(db_session, user) == 0
    assert await _points(db_session, user) == 120


async def test_a_lot_expires_at_the_end_of_the_ist_day_a_year_later() -> None:
    earned = datetime(2026, 10, 10, 20, 0, tzinfo=UTC)  # 11 Oct, 01:30 IST

    expires = kindness.expiry(earned).astimezone(kindness.IST)

    assert (expires.date().isoformat(), expires.hour, expires.minute) == ("2027-10-11", 23, 59)


async def test_bad_credits_are_refused(db_session: AsyncSession) -> None:
    user, source = uuid.uuid4(), uuid.uuid4()
    for points, source_type in ((0, "referral"), (-5, "referral"), (10, "Referral!")):
        with pytest.raises(ValueError):
            await billing_api.credit_points(db_session, user, points, source_type, source)
    await billing_api.credit_points(db_session, user, 10, "donation", source)
    with pytest.raises(ProblemError) as conflict:
        await billing_api.credit_points(db_session, user, 20, "donation", source)
    assert conflict.value.code == "billing.entry_conflict"
