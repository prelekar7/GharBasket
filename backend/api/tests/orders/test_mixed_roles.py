"""A person who is both a customer and staff of another hub (handbook §13.4): her customer role
reaches only her own orders and her staff role only her own hub, with or without X-Active-Role."""

import uuid

import pytest
import time_machine
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.identity.models import UserRole
from gharbasket.modules.orders import api as orders_api
from gharbasket.tools.seed import SEED_HUB_ID

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .test_orders import NOW, PRIYA, SUNITA, VIKRAM, approved_cycle, as_user, make_slot

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("world")]

OTHER_HUB = uuid.UUID("0199a000-0000-7000-8000-00000000a0fe")
OTHER_STAFF = f"hub_manager:{OTHER_HUB}"


@time_machine.travel(NOW, tick=False)
async def test_staff_of_another_hub_cannot_read_a_customers_order_with_any_active_role(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    _, priya_id = await as_user(api, google, PRIYA)
    _, sunita_id = await as_user(api, google, SUNITA)
    vikram, _ = await as_user(api, google, VIKRAM)
    slot = uuid.UUID((await make_slot(api, vikram, capacity=2))["id"])
    orders = {}
    for name, user in (("priya", priya_id), ("sunita", sunita_id)):
        household = await identity_api.household_for_user(db_session, user)
        assert household is not None
        cycle = await approved_cycle(db_session, household, slot, "tomato")
        orders[name] = str((await orders_api.create_from_cycle(db_session, cycle)).id)
    # Sunita (a customer of the seed hub) also manages another hub.
    db_session.add(
        UserRole(user_id=sunita_id, role="hub_manager", scope_type="hub", scope_id=OTHER_HUB)
    )
    await db_session.commit()
    sunita, _ = await as_user(api, google, SUNITA)

    async def read(order: str, active: str | None) -> tuple[int, str | None]:
        headers = sunita if active is None else {**sunita, "X-Active-Role": active}
        answer = await api.get(f"/v1/orders/{order}", headers=headers)
        return answer.status_code, answer.json().get("code")

    # Priya's order is in the seed hub: neither of Sunita's roles reaches it.
    assert await read(orders["priya"], None) == (404, "orders.not_found")
    assert await read(orders["priya"], "customer") == (404, "orders.not_found")
    assert await read(orders["priya"], OTHER_STAFF) == (403, "auth.forbidden")
    # Her own order stays hers as a customer; as the other hub's manager it is not her hub's.
    assert (await read(orders["sunita"], None))[0] == 200
    assert (await read(orders["sunita"], "customer"))[0] == 200
    assert await read(orders["sunita"], OTHER_STAFF) == (403, "auth.forbidden")
    # The seed hub's order list is closed to her, whichever role she picks.
    for active in (None, OTHER_STAFF):
        headers = sunita if active is None else {**sunita, "X-Active-Role": active}
        listed = await api.get(f"/v1/orders?hub_id={SEED_HUB_ID}", headers=headers)
        assert (listed.status_code, listed.json()["code"]) == (403, "auth.forbidden")
    as_customer = await api.get(
        f"/v1/orders?hub_id={SEED_HUB_ID}", headers={**sunita, "X-Active-Role": "customer"}
    )
    assert [o["id"] for o in as_customer.json()["items"]] == [orders["sunita"]]
    # The seed hub's own manager (customer + hub_manager of the seed hub) still reads it.
    assert (await api.get(f"/v1/orders/{orders['priya']}", headers=vikram)).status_code == 200
