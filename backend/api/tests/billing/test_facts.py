"""billing.api.earning_facts and kindness_facts: the ledger read back for M12 (M12 §3.1)."""

import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.billing import kindness

pytestmark = [pytest.mark.anyio, pytest.mark.services]

IST = ZoneInfo("Asia/Kolkata")
MONDAY = date(2026, 11, 2)


def week(day: date) -> str:
    year, number, _ = day.isocalendar()
    return f"{year}-W{number:02d}"


async def test_earning_facts_sum_each_partner_week_and_basis(db_session: AsyncSession) -> None:
    kitchen, captain = uuid.uuid4(), uuid.uuid4()
    sunday_night = datetime(2026, 11, 8, 23, 30, tzinfo=IST)  # still the week of MONDAY in IST
    for partner, basis, units, paise, at in (
        (captain, "bag_delivered", 1, 600, datetime(2026, 11, 3, 8, 0, tzinfo=IST)),
        (captain, "bag_delivered", 1, 600, sunday_night),
        (kitchen, "tiffin_portion", 3, 9_000, datetime(2026, 11, 4, 12, 0, tzinfo=IST)),
        (kitchen, "tiffin_portion", 2, 6_000, datetime(2026, 11, 9, 12, 0, tzinfo=IST)),
    ):
        await billing_api.accrue_earning(
            db_session,
            partner_id=partner,
            basis=basis,
            ref_type="order",
            ref_id=uuid.uuid4(),
            units=units,
            amount_paise=paise,
            earned_at=at,
        )

    this_week = await billing_api.earning_facts(
        db_session, week_from=week(MONDAY), week_to=week(MONDAY)
    )
    both = await billing_api.earning_facts(
        db_session, week_from=week(MONDAY), week_to=week(MONDAY + timedelta(days=7))
    )

    mine = [f for f in this_week if f.partner_id in (kitchen, captain)]
    assert sorted((f.basis, f.units, f.amount_paise) for f in mine) == [
        ("bag_delivered", 2, 1_200),
        ("tiffin_portion", 3, 9_000),
    ]
    assert {f.iso_week for f in mine} == {week(MONDAY)}
    assert sorted((f.iso_week, f.units) for f in both if f.partner_id == kitchen) == [
        (week(MONDAY), 3),
        (week(MONDAY + timedelta(days=7)), 2),
    ]


async def test_kindness_facts_split_credited_donated_and_expired(db_session: AsyncSession) -> None:
    user = uuid.uuid4()
    await billing_api.credit_points(db_session, user, 300, "donation", uuid.uuid4())
    await kindness.donate(db_session, user, 1, 250, "facts-key")
    await kindness.expire_lots(db_session, user, datetime.now(UTC) + timedelta(days=400))
    today = datetime.now(IST).date()

    facts = await billing_api.kindness_facts(db_session, date_from=today, date_to=today)

    rows = {(f.on, f.source_type): f for f in facts}
    donation = rows[(today, "donation")]
    meals = rows[(today, None)]
    assert (donation.credited, donation.donated, donation.expired) == (300, 0, 50)
    assert (meals.credited, meals.donated, meals.expired, meals.meals) == (0, 250, 0, 1)
    assert (
        await billing_api.kindness_facts(
            db_session, date_from=today + timedelta(days=1), date_to=today + timedelta(days=2)
        )
        == []
    )
