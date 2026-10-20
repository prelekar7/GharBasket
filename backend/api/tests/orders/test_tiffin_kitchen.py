"""The kitchen on a tiffin (ADR-0026 §3) and a locked tiffin no kitchen took, refunded in full
through M5 (ADR-0027 §1): M4's facade for M8."""

import uuid
from datetime import UTC, date, datetime

import pytest
import time_machine
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.billing.models import JournalEntry
from gharbasket.modules.billing.wallet import credit_topup
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.orders import service
from gharbasket.modules.orders.models import OrderEvent
from gharbasket.platform.errors import ProblemError
from gharbasket.tools.seed import SEED_HUB_ID

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .test_orders import PRIYA, approved_cycle, as_user, make_slot
from .test_tiffin_orders import book, tiffin_slot
from .tiffin_menus import menu

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("world")]

NOW = datetime(2026, 10, 10, 10, 0, tzinfo=UTC)  # Sat 15:30 IST
DAY = date(2026, 10, 12)


@time_machine.travel(NOW, tick=False)
async def test_m8_records_the_kitchen_and_refunds_a_tiffin_nobody_took(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await tiffin_slot(db_session)
    cook = uuid.uuid4()
    lunch = (await menu(db_session, DAY, partner_id=cook, price_paise=11_000)).id
    await db_session.commit()
    api = clients()
    priya, priya_id = await as_user(api, google, PRIYA)
    booked = await book(api, priya, menu_id=str(lunch), portions=2)
    order_id = uuid.UUID(booked.json()["id"])
    await credit_topup(
        db_session, priya_id, 50_000, payment_ref=uuid.uuid4().hex, topup_id=uuid.uuid4()
    )
    await db_session.commit()
    await api.post("/v1/internal/jobs/cutoff_lock", json={"scheduled_for": "2026-10-11T15:30:00Z"})
    paid = await billing_api.balance(db_session, priya_id)

    # M8 reads the preferred cook from orders_for_delivery, and records who accepted.
    (summary,) = await orders_api.orders_for_delivery(
        db_session, SEED_HUB_ID, DAY, kinds=("tiffin",)
    )
    assert summary.order.preferred_partner_id == cook
    other = uuid.uuid4()
    await orders_api.set_kitchen(db_session, order_id, other)
    shown = await api.get(f"/v1/orders/{order_id}", headers=priya)
    assert shown.json()["kitchen"] == {"partner_id": str(other)}
    version = shown.json()["version"]
    await orders_api.set_kitchen(db_session, order_id, other)  # the same again: no change
    await orders_api.set_kitchen(db_session, order_id, None)  # cancelled after acceptance
    cleared = await api.get(f"/v1/orders/{order_id}", headers=priya)
    assert cleared.json()["kitchen"] is None and cleared.json()["version"] == version + 1

    with pytest.raises(ProblemError) as by_kitchen:
        await orders_api.transition(db_session, order_id, "refunded", actor="kitchen")
    assert by_kitchen.value.code == "orders.illegal_transition"
    await orders_api.transition(
        db_session, order_id, "refunded", actor="system", payload={"reason": "no_kitchen"}
    )

    assert (await orders_api.get(db_session, order_id)).status == "refunded"
    assert await billing_api.balance(db_session, priya_id) == paid + 22_000
    keys = await db_session.execute(
        select(JournalEntry.idempotency_key).where(
            JournalEntry.ref_id == order_id, JournalEntry.kind == "refund"
        )
    )
    assert keys.scalars().all() == [f"refund:{order_id}:no_kitchen"]
    move = await db_session.scalar(
        select(OrderEvent).where(
            OrderEvent.order_id == order_id, OrderEvent.to_status == "refunded"
        )
    )
    assert move is not None and move.payload == {"reason": "no_kitchen", "refund_paise": 22_000}


@time_machine.travel(NOW, tick=False)
async def test_only_a_tiffin_has_a_kitchen_or_the_no_kitchen_refund(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    vikram_auth, _ = await as_user(api, google, "+919800001008")
    slot = await make_slot(api, vikram_auth, label="Box 7-9")
    _, priya_id = await as_user(api, google, PRIYA)
    household = await identity_api.household_for_user(db_session, priya_id)
    assert household is not None
    order = await orders_api.create_from_cycle(
        db_session, await approved_cycle(db_session, household, uuid.UUID(slot["id"]), "tomato")
    )
    await service.transition(db_session, order, "locked", actor="system")

    with pytest.raises(ProblemError) as no_kitchen:
        await orders_api.set_kitchen(db_session, order.id, uuid.uuid4())
    with pytest.raises(ProblemError) as box_refund:
        await orders_api.transition(
            db_session, order.id, "refunded", actor="system", payload={"reason": "no_kitchen"}
        )

    assert no_kitchen.value.code == "orders.not_editable"
    assert box_refund.value.code == "orders.illegal_transition"
