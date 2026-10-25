"""`orders.api.hub_cutoff_counts`: what the cut-off did to a hub's box orders on a delivery day,
the numbers behind M3's cut-off monitor (M3 §3.4)."""

import uuid
from datetime import UTC, datetime

import pytest
import time_machine
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing.wallet import credit_topup
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.orders.jobs import on_wallet_topped_up
from gharbasket.platform.models import OutboxEvent
from gharbasket.tools.seed import SEED_HUB_ID

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from ..subscriptions.test_cycles import MEENA, NOW, PRIYA, as_user
from .test_cutoff import CUTOFF_AT, GRACE_END, SUNITA, approved_box, run

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("delivery")]

RAMESH = "+919800001003"
TUESDAY = datetime(2026, 10, 13).date()


@time_machine.travel(NOW, tick=False)
async def test_counts_of_locked_failed_paid_in_grace_and_cancelled_orders(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, priya_id = await as_user(api, google, PRIYA)
    meena, meena_id = await as_user(api, google, MEENA)
    sunita, _ = await as_user(api, google, SUNITA)
    ramesh, _ = await as_user(api, google, RAMESH)
    priya_cycle, _priya_order, priya_total = await approved_box(api, priya)
    meena_cycle, meena_order, meena_total = await approved_box(api, meena)
    sunita_cycle, sunita_order, _ = await approved_box(api, sunita)
    ramesh_cycle, ramesh_order, ramesh_total = await approved_box(api, ramesh)

    empty = await orders_api.hub_cutoff_counts(db_session, SEED_HUB_ID, TUESDAY)
    assert (empty.locked, empty.payment_needed, empty.grace_paid, empty.cancelled) == (0, 0, 0, 0)
    assert empty.settled_cycle_ids == frozenset() and empty.payment_failed == ()

    await credit_topup(db_session, priya_id, priya_total, payment_ref="p1", topup_id=uuid.uuid4())
    await credit_topup(db_session, meena_id, 1_000, payment_ref="m1", topup_id=uuid.uuid4())
    with time_machine.travel(datetime(2026, 10, 12, 15, 30, tzinfo=UTC), tick=False):
        await run(api, "cutoff_lock", CUTOFF_AT)
    db_session.expire_all()

    failed = await orders_api.hub_cutoff_counts(db_session, SEED_HUB_ID, TUESDAY)
    assert (failed.locked, failed.payment_needed, failed.grace_paid, failed.cancelled) == (
        1,
        3,
        0,
        0,
    )
    assert failed.settled_cycle_ids == {priya_cycle, meena_cycle, sunita_cycle, ramesh_cycle}
    assert {o.order_id: (o.cycle_id, o.total_paise) for o in failed.payment_failed}[
        ramesh_order
    ] == (ramesh_cycle, ramesh_total)
    assert {o.order_id for o in failed.payment_failed} == {meena_order, sunita_order, ramesh_order}
    # Not the hub's, not that day.
    other_hub = await orders_api.hub_cutoff_counts(db_session, uuid.uuid4(), TUESDAY)
    other_day = await orders_api.hub_cutoff_counts(
        db_session, SEED_HUB_ID, datetime(2026, 10, 20).date()
    )
    assert other_hub.settled_cycle_ids == other_day.settled_cycle_ids == frozenset()

    # Meena pays inside the grace period; Sunita's grace ends unpaid; Ramesh is skipped by the
    # customer (reason "skipped", not "payment").
    await credit_topup(db_session, meena_id, meena_total, payment_ref="m2", topup_id=uuid.uuid4())
    await on_wallet_topped_up(
        db_session,
        OutboxEvent(event_type="billing.wallet_topped_up", payload={"user_id": str(meena_id)}),
    )
    await orders_api.cancel_from_cycle(db_session, ramesh_cycle)
    with time_machine.travel(datetime(2026, 10, 12, 17, 30, tzinfo=UTC), tick=False):
        await run(api, "payment_grace_expiry", GRACE_END)
    db_session.expire_all()

    done = await orders_api.hub_cutoff_counts(db_session, SEED_HUB_ID, TUESDAY)
    assert (done.locked, done.payment_needed, done.grace_paid, done.cancelled) == (2, 0, 1, 1)
    # The grace job cancelled Sunita's and Ramesh's too (cancel_from_cycle moved him first, so
    # only the "payment" one counts); her cycle stays on the order side until the mirror runs.
    assert done.settled_cycle_ids == {priya_cycle, meena_cycle, sunita_cycle}
    assert done.payment_failed == ()
