"""community.api.impact_facts and referral_facts by conversion: M12's impact rows (M12 §3.1)."""

import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community import api as community_api
from gharbasket.modules.community.models import Donation, Referral, Society
from gharbasket.tools.seed import SEED_HUB_ID, SEED_SOCIETY_ID

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("societies")]

IST = ZoneInfo("Asia/Kolkata")
TUESDAY = date(2026, 11, 3)
AT = datetime(2026, 11, 3, 23, 50, tzinfo=IST)  # late on Tuesday IST, Tuesday evening UTC


def _donation(status: str, items: list[dict[str, object]], **values: object) -> Donation:
    return Donation(
        user_id=uuid.uuid4(),
        household_id=uuid.uuid4(),
        hub_id=SEED_HUB_ID,
        society_id=SEED_SOCIETY_ID,
        items=items,
        status=status,
        pickup_on=TUESDAY,
        estimated_points=100,
        **values,
    )


async def test_impact_facts_count_verified_donations_and_societies_going_live(
    db_session: AsyncSession,
) -> None:
    verified = _donation(
        "verified",
        [
            {"code": "clothes", "unit": "kg", "qty": 3, "counted": 2.5, "rate": 40, "points": 100},
            {"code": "books", "unit": "each", "qty": 4, "counted": 3, "rate": 10, "points": 30},
        ],
        points_awarded=130,
        verified_at=AT,
        data_source="synthetic",
    )
    rejected = _donation("rejected", [{"code": "books", "unit": "each", "qty": 1}], verified_at=AT)
    waiting = _donation("picked_up", [{"code": "books", "unit": "each", "qty": 1}])
    db_session.add_all([verified, rejected, waiting])
    society = await db_session.get(Society, SEED_SOCIETY_ID)
    assert society is not None
    society.live_at = AT
    await db_session.flush()

    facts = await community_api.impact_facts(db_session, date_from=TUESDAY, date_to=TUESDAY)

    assert [(f.kind, f.source, f.source_id) for f in facts] == [
        ("donation_verified", "donation", verified.id),
        ("society_live", "society", SEED_SOCIETY_ID),
    ]
    donation, live = facts
    assert (donation.items, donation.kg_tenths, donation.points) == (6, 25, 130)  # 5.5 half up
    assert (donation.on, donation.household_id, donation.data_source) == (
        TUESDAY,
        verified.household_id,
        "synthetic",
    )
    assert (donation.ngo_id, donation.people_reached, donation.meals) == (None, None, 0)
    assert (live.on, live.hub_id, live.household_id, live.items) == (TUESDAY, SEED_HUB_ID, None, 0)
    wednesday = TUESDAY + timedelta(days=1)
    assert not await community_api.impact_facts(db_session, date_from=wednesday, date_to=wednesday)
    assert (
        await community_api.impact_facts(
            db_session, date_from=TUESDAY, date_to=TUESDAY, hub_ids=[uuid.uuid4()]
        )
        == []
    )


async def test_referral_facts_can_be_read_by_the_conversion_date(db_session: AsyncSession) -> None:
    referral = Referral(
        code="ABC123",
        referrer_user_id=uuid.uuid4(),
        referee_user_id=uuid.uuid4(),
        referee_household_id=uuid.uuid4(),
        society_id=SEED_SOCIETY_ID,
        hub_id=SEED_HUB_ID,
        status="converted",
        joined_on=TUESDAY - timedelta(days=20),
        converted_on=TUESDAY,
        referrer_points=250,
        referee_points=100,
    )
    db_session.add(referral)
    await db_session.flush()

    by_joined = await community_api.referral_facts(db_session, date_from=TUESDAY, date_to=TUESDAY)
    by_converted = await community_api.referral_facts(
        db_session, date_from=TUESDAY, date_to=TUESDAY, by="converted"
    )

    assert referral.id not in {f.referral_id for f in by_joined}
    assert [(f.referral_id, f.converted_on, f.referee_points) for f in by_converted] == [
        (referral.id, TUESDAY, 100)
    ]
