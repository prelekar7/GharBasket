"""`orders.api.box_slots` (M4 §3.9): the hub's active box slots with capacity, bookings and room
on a date, as `GET /v1/slots` counts them, for any date (M9 prices past days)."""

from datetime import date, time

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.identity.service import find_or_create_user
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.orders import service
from gharbasket.tools.seed import SEED_HUB_ID

from .test_orders import DELIVERY, PRIYA, approved_cycle

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("world")]


async def test_box_slots_count_bookings_and_leave_out_tiffin_and_inactive_slots(
    db_session: AsyncSession,
) -> None:
    late = await service.create_slot(
        db_session,
        hub_id=SEED_HUB_ID,
        label="Box 9-11",
        kind="box",
        start=time(9),
        end=time(11),
        capacity=5,
    )
    early = await service.create_slot(
        db_session,
        hub_id=SEED_HUB_ID,
        label="Box 7-9",
        kind="box",
        start=time(7),
        end=time(9),
        capacity=2,
    )
    off = await service.create_slot(
        db_session,
        hub_id=SEED_HUB_ID,
        label="Box off",
        kind="box",
        start=time(8),
        end=time(10),
        capacity=5,
    )
    off.active = False
    lunch = await service.create_slot(
        db_session,
        hub_id=SEED_HUB_ID,
        label="Lunch",
        kind="tiffin",
        start=time(12),
        end=time(13),
        capacity=5,
        meal="lunch",
    )
    user, _ = await find_or_create_user(db_session, PRIYA)
    household = await identity_api.household_for_user(db_session, user.id)
    assert household is not None
    await orders_api.create_from_cycle(
        db_session, await approved_cycle(db_session, household, early.id, "tomato")
    )

    slots = await orders_api.box_slots(db_session, SEED_HUB_ID, DELIVERY)
    past = await orders_api.box_slots(db_session, SEED_HUB_ID, date(2026, 1, 6))

    ours = [s for s in slots if s.id in (early.id, late.id)]
    assert [(s.label, s.capacity, s.booked, s.available) for s in ours] == [
        ("Box 7-9", 2, 1, 1),
        ("Box 9-11", 5, 0, 5),
    ]
    assert (ours[0].start, ours[0].end) == (time(7), time(9))
    assert {off.id, lunch.id}.isdisjoint(s.id for s in slots)
    assert [s.booked for s in past if s.id == early.id] == [0]
