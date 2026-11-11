"""M4's ops probe `orders.revenue_unrecognised` (M5 §3.9, ADR-0033): a box delivered more than an
hour ago that still holds money in M5's unearned_revenue is reported, until M5's consumer of
`orders.delivered` recognises it."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import jobs as billing_jobs
from gharbasket.modules.orders import jobs
from gharbasket.platform.models import OutboxEvent

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .test_carry_over import AT_THE_DOOR, MISSED, NEXT, household, place

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("world")]


async def test_a_delivered_box_still_holding_unearned_money_is_reported_until_recognised(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    _, _, owner = await household(clients, google)
    first = await place(db_session, owner, MISSED, (*AT_THE_DOOR[:-1], "delivered"))
    await place(db_session, owner, NEXT, (*AT_THE_DOOR[:-1], "delivered"))
    later = datetime.now(UTC) + timedelta(hours=2)  # the moves are stamped by the database now

    assert await jobs.revenue_unrecognised(db_session, datetime.now(UTC)) == []  # under an hour
    assert await jobs.revenue_unrecognised(db_session, later) == [
        "2 delivered orders still hold unearned revenue; run recognise_delivered_revenue"
    ]
    assert await jobs.revenue_unrecognised(db_session, later + timedelta(days=8)) == []
    events = await db_session.scalars(
        select(OutboxEvent).where(OutboxEvent.event_type == "orders.delivered")
    )
    for event in events:
        if event.payload["order_id"] == str(first):
            await billing_jobs.on_order_delivered(db_session, event)
    assert await jobs.revenue_unrecognised(db_session, later) == [
        "1 delivered order still holds unearned revenue; run recognise_delivered_revenue"
    ]
