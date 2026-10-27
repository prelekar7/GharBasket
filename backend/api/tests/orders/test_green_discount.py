"""The green-slot discount on a box order (M4 §3.9): at most the subtotal, set again on every
re-approval, debited net once at the cut-off (a society that goes live keeps it and drops the
fee), and a re-approval with another slot moves the booking under that slot's lock."""

import uuid
from dataclasses import replace
from datetime import time

import pytest
import time_machine
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.billing.wallet import credit_topup
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.orders import service
from gharbasket.platform.errors import ProblemError
from gharbasket.tools.seed import SEED_HUB_ID

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .test_fees import FEE, debits, hub, lock, set_live  # noqa: F401 (hub: the fixture)
from .test_orders import DELIVERY, NOW, PRIYA, SUNITA, approved_cycle, as_user

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("world", "hub")]

SAVING = 1_500


async def slot(db: AsyncSession, capacity: int = 10) -> uuid.UUID:
    made = await service.create_slot(
        db,
        hub_id=SEED_HUB_ID,
        label=f"Green {uuid.uuid4().hex[:8]}",
        kind="box",
        start=time(7),
        end=time(9),
        capacity=capacity,
    )
    return made.id


async def cycle_of(db: AsyncSession, user_id: uuid.UUID, slot_id: uuid.UUID, **changes):
    household = await identity_api.household_for_user(db, user_id)
    assert household is not None
    return replace(await approved_cycle(db, household, slot_id, "tomato", "bhindi"), **changes)


async def booked(db: AsyncSession) -> dict[uuid.UUID, int]:
    return {s.id: s.booked for s in await orders_api.box_slots(db, SEED_HUB_ID, DELIVERY)}


@time_machine.travel(NOW, tick=False)
async def test_the_discount_is_capped_at_the_subtotal_and_debited_net_once(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    _, sunita_id = await as_user(api, google, SUNITA)  # an unlocking society: the fee applies
    cycle = await cycle_of(
        db_session,
        sunita_id,
        await slot(db_session),
        discount_paise=SAVING,
        discount_code="green_slot",
    )
    order = await orders_api.create_from_cycle(db_session, cycle)
    assert (order.discount_paise, order.discount_code, order.fees_paise) == (
        SAVING,
        "green_slot",
        FEE,
    )
    assert order.total_paise == order.subtotal_paise + FEE - SAVING

    huge = await orders_api.create_from_cycle(
        db_session, replace(cycle, version=2, discount_paise=10**7)
    )
    assert (huge.discount_paise, huge.total_paise) == (huge.subtotal_paise, FEE)
    none = await orders_api.create_from_cycle(
        db_session, replace(cycle, version=3, discount_paise=0, discount_code=None)
    )
    assert (none.discount_paise, none.discount_code) == (0, None)
    again = await orders_api.create_from_cycle(db_session, replace(cycle, version=4))
    order_id, subtotal = again.id, again.subtotal_paise
    assert again.total_paise == subtotal + FEE - SAVING
    await credit_topup(
        db_session, sunita_id, again.total_paise, payment_ref="g1", topup_id=uuid.uuid4()
    )
    await set_live(db_session, again.society_id, live=True)  # before the lock: no fee

    await lock(api)
    await lock(api)

    db_session.expire_all()
    locked = await orders_api.get(db_session, order_id)
    assert (locked.status, locked.fees_paise, locked.discount_paise) == ("locked", 0, SAVING)
    assert locked.total_paise == subtotal - SAVING
    assert len(await debits(db_session, order_id)) == 1
    assert await billing_api.balance(db_session, sunita_id) == FEE  # the fee was not taken
    # A whole-box refund returns what was paid: the lines less the saving.
    assert await orders_api.refundable_paise(db_session, order_id) == subtotal - SAVING


@time_machine.travel(NOW, tick=False)
async def test_a_reapproval_with_another_slot_moves_the_booking_unless_it_is_full(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    _, priya_id = await as_user(api, google, PRIYA)
    _, sunita_id = await as_user(api, google, SUNITA)
    first, second, full = await slot(db_session), await slot(db_session), await slot(db_session, 1)
    await orders_api.create_from_cycle(db_session, await cycle_of(db_session, sunita_id, full))
    cycle = await cycle_of(db_session, priya_id, first)
    order = await orders_api.create_from_cycle(db_session, cycle)
    version = order.version
    await db_session.commit()

    moved = await orders_api.create_from_cycle(
        db_session, replace(cycle, version=2, slot_id=second)
    )
    assert (moved.slot_id, moved.delivery_date) == (second, DELIVERY)
    assert moved.version == version + 1
    assert (await booked(db_session))[first] == 0 and (await booked(db_session))[second] == 1

    with pytest.raises(ProblemError) as refused:
        await orders_api.create_from_cycle(db_session, replace(cycle, version=3, slot_id=full))
    assert refused.value.code == "orders.slot_full"
    assert (await orders_api.get(db_session, order.id)).slot_id == second
    loads = await booked(db_session)
    assert (loads[second], loads[full]) == (1, 1)
