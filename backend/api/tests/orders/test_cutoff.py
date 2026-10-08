"""The cut-off handshake (M4 §3.1): debit and lock, payment failed with a grace period, a top-up
inside it, cancellation after it, and the cycle mirroring its order (M3 §5)."""

import uuid
from datetime import UTC, datetime

import httpx2
import pytest
import time_machine
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.billing.wallet import credit_topup
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.orders.jobs import on_wallet_topped_up
from gharbasket.modules.subscriptions.models import WeeklyCycle
from gharbasket.platform.models import OutboxEvent
from gharbasket.platform.outbox import relay

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from ..subscriptions.test_cycles import GENERATE_AT, MEENA, NOW, PRIYA, as_user, subscribe

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("delivery")]

SUNITA = "+919800001002"
CUTOFF_AT = "2026-10-12T15:30:00Z"  # Mon 21:00 IST for the Tuesday box
GRACE_END = "2026-10-12T17:30:00Z"


async def approved_box(api: httpx2.AsyncClient, auth: dict) -> tuple[uuid.UUID, uuid.UUID, int]:
    """Subscribe, take this week's box as drafted and approve it: (cycle, order, total)."""
    await subscribe(api, auth)
    answer = await api.post(
        "/v1/internal/jobs/generate_weekly_plans", json={"scheduled_for": GENERATE_AT}
    )
    assert answer.status_code == 200, answer.text
    box = (await api.get("/v1/cycles/current", headers=auth)).json()
    approved = await api.post(
        f"/v1/cycles/{box['id']}/approve",
        headers={**auth, "Idempotency-Key": str(uuid.uuid4()), "If-Match": '"1"'},
    )
    assert approved.status_code == 200, approved.text
    body = approved.json()
    return uuid.UUID(body["id"]), uuid.UUID(body["order_id"]), body["total_paise"]


async def run(api: httpx2.AsyncClient, name: str, at: str) -> dict:
    answer = await api.post(f"/v1/internal/jobs/{name}", json={"scheduled_for": at})
    assert answer.status_code == 200, answer.text
    return answer.json()["summary"]


@time_machine.travel(NOW, tick=False)
async def test_the_cutoff_debits_locks_and_gives_the_unpaid_a_grace_period(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, priya_id = await as_user(api, google, PRIYA)
    meena, meena_id = await as_user(api, google, MEENA)
    sunita, _ = await as_user(api, google, SUNITA)
    priya_cycle, priya_order, priya_total = await approved_box(api, priya)
    meena_cycle, meena_order, meena_total = await approved_box(api, meena)
    sunita_cycle, sunita_order, _ = await approved_box(api, sunita)
    await credit_topup(
        db_session, priya_id, priya_total + 1_000, payment_ref="p1", topup_id=uuid.uuid4()
    )
    await credit_topup(db_session, meena_id, 1_000, payment_ref="m1", topup_id=uuid.uuid4())

    with time_machine.travel(datetime(2026, 10, 12, 15, 30, tzinfo=UTC), tick=False):
        summary = await run(api, "cutoff_lock", CUTOFF_AT)
        again = await run(api, "cutoff_lock", CUTOFF_AT)
    assert summary == {
        "locked": 1,
        "payment_failed": 2,
        "skipped_already_locked": 0,
        "delivery_date": "2026-10-13",
    }
    assert again["locked"] == 0 and again["skipped_already_locked"] == 1
    db_session.expire_all()
    assert (await orders_api.get(db_session, priya_order)).status == "locked"
    assert (await orders_api.get(db_session, meena_order)).status == "payment_failed"
    assert await billing_api.balance(db_session, priya_id) == 1_000
    failed = [
        e.payload
        for e in (await db_session.execute(select(OutboxEvent))).scalars()
        if e.event_type == "orders.payment_failed"
    ]
    assert len(failed) == 2 and all(f["grace_until"] == "2026-10-12T17:30:00+00:00" for f in failed)
    assert {f["shortfall_paise"] for f in failed if f["order_id"] == str(meena_order)} == {
        meena_total - 1_000
    }

    # Meena tops up inside the grace period: her order is paid and locked.
    await credit_topup(db_session, meena_id, meena_total, payment_ref="m2", topup_id=uuid.uuid4())
    await on_wallet_topped_up(
        db_session,
        OutboxEvent(event_type="billing.wallet_topped_up", payload={"user_id": str(meena_id)}),
    )
    db_session.expire_all()
    assert (await orders_api.get(db_session, meena_order)).status == "locked"

    # Sunita does not: the grace job cancels hers with reason "payment".
    with time_machine.travel(datetime(2026, 10, 12, 17, 30, tzinfo=UTC), tick=False):
        expired = await run(api, "payment_grace_expiry", GRACE_END)
    assert expired == {"cancelled": 1, "delivery_date": "2026-10-13"}
    db_session.expire_all()
    assert (await orders_api.get(db_session, sunita_order)).status == "cancelled"

    # The cycles mirror their orders once the events are delivered.
    await relay(db_session)
    cycles = {c.id: c for c in (await db_session.execute(select(WeeklyCycle))).scalars()}
    assert (cycles[priya_cycle].status, cycles[priya_cycle].order_id) == ("locked", priya_order)
    assert cycles[meena_cycle].status == "locked"
    assert cycles[sunita_cycle].status == "cancelled"
