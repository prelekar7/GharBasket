"""A tiffin booked through M4 (§3.5) marks that meal `tiffin` in the week, and cancelling it puts
the plan's dish back, while the week can still be edited."""

import uuid
from datetime import date, time

import pytest
import time_machine
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.orders import service as orders_service
from gharbasket.modules.subscriptions import service
from gharbasket.platform.outbox import relay
from gharbasket.tools.seed import SEED_HUB_ID

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from ..orders.tiffin_menus import menu
from .test_cycles import NOW, PRIYA, as_user, subscribe
from .test_edits import generate, key

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("delivery")]

WEDNESDAY = date(2026, 10, 14)


def lunch_of(box: dict, on: date) -> dict:
    day = next(d for d in box["meal_plan"]["days"] if d["date"] == on.isoformat())
    return next(m for m in day["meals"] if m["slot"] == "lunch")


@time_machine.travel(NOW, tick=False)
async def test_a_booked_tiffin_marks_the_meal_and_a_cancelled_one_restores_it(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, priya_id = await as_user(api, google, PRIYA)
    await subscribe(api, priya)
    await generate(api)
    planned = lunch_of((await api.get("/v1/cycles/current", headers=priya)).json(), WEDNESDAY)
    await orders_service.create_slot(
        db_session,
        hub_id=SEED_HUB_ID,
        label="Lunch tiffins",
        kind="tiffin",
        start=time(12, 30),
        end=time(13, 30),
        capacity=20,
        meal="lunch",
    )
    offered = (await menu(db_session, WEDNESDAY)).id
    await db_session.commit()

    booked = await api.post(
        "/v1/orders",
        json={"kind": "tiffin", "menu_id": str(offered), "portions": 1},
        headers={**priya, **key()},
    )
    assert booked.status_code == 201, booked.text
    while (await relay(db_session)).sent:
        pass
    marked = lunch_of((await api.get("/v1/cycles/current", headers=priya)).json(), WEDNESDAY)
    assert (marked["tiffin"], marked["recipe_id"], marked["source"]) == (True, None, "customer")

    cancelled = await api.post(
        f"/v1/orders/{booked.json()['id']}/cancel", headers={**priya, **key()}
    )
    assert cancelled.status_code == 200, cancelled.text
    while (await relay(db_session)).sent:
        pass
    restored = lunch_of((await api.get("/v1/cycles/current", headers=priya)).json(), WEDNESDAY)
    assert (restored["tiffin"], restored["recipe_id"]) == (False, planned["recipe_id"])

    # Nothing to do: another household, or a meal outside any editable week.
    assert not await service.mark_tiffin_meal(
        db_session, uuid.uuid4(), WEDNESDAY, "lunch", booked=True
    )
    assert not await service.mark_tiffin_meal(
        db_session, priya_id, date(2026, 12, 1), "lunch", booked=True
    )
