"""What M13's `track_order` reads from M7 (M13 §3.1): the same status, window and estimate as the
customer's delivery card, and nothing of the card's private parts."""

import dataclasses

import pytest
import time_machine
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.logistics import api as logistics_api
from gharbasket.modules.orders import api as orders_api

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .conftest import MORNING, PRIYA, SUNITA, as_user, published_day
from .test_doors import started

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("world")]


@time_machine.travel(MORNING, tick=False)
async def test_before_the_van_leaves_the_estimate_is_the_plans_and_nothing_is_on_the_way(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    day = await published_day(db_session)
    priya = await as_user(api, google, PRIYA)
    orders = [await orders_api.get(db_session, day.orders[p]) for p in (PRIYA, SUNITA)]

    signals = await logistics_api.delivery_signals(db_session, orders)

    card = (await api.get(f"/v1/deliveries/{orders[0].id}", headers=priya)).json()
    mine = signals[orders[0].id]
    assert (mine.status, mine.eta, mine.on_the_way) == (card["status"], card["eta"], False)
    assert mine.status == "scheduled" and mine.eta is not None
    assert mine.window == (card["window"]["start"], card["window"]["end"])
    later = signals[orders[1].id].window  # Sunita's slot starts at 09:00
    assert later is not None and later[0] == "09:00"


@time_machine.travel(MORNING, tick=False)
async def test_while_the_van_has_the_order_it_is_on_the_way_and_the_card_agrees(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    day, _ = await started(api, google, db_session)
    priya = await as_user(api, google, PRIYA)
    order = await orders_api.get(db_session, day.orders[PRIYA])

    mine = (await logistics_api.delivery_signals(db_session, [order]))[order.id]

    card = (await api.get(f"/v1/deliveries/{order.id}", headers=priya)).json()
    assert order.status == "dispatched" and card["tracking"] is not None
    assert mine.on_the_way is True and mine.eta == card["eta"] and mine.status == card["status"]
    # Only status, window, estimate and the on-the-way flag: no captain, code, proof or route.
    assert {f.name for f in dataclasses.fields(mine)} == {"status", "window", "eta", "on_the_way"}
    assert "delivery_code" not in repr(mine) and str(day.route_id) not in repr(mine)
