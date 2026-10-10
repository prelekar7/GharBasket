"""Paying a box the cut-off could not debit (M4 §3.4) and the week's amount due (M3 §3.3): one
debit under replays and a race with the top-up's retry, and the refusals."""

import uuid
from datetime import UTC, datetime

import httpx2
import pytest
import time_machine
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.billing.models import JournalEntry
from gharbasket.modules.billing.wallet import credit_topup
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.orders.jobs import on_wallet_topped_up
from gharbasket.platform.models import OutboxEvent
from gharbasket.platform.outbox import relay

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from ..subscriptions.test_cycles import MEENA, NOW, PRIYA, as_user
from .test_cutoff import CUTOFF_AT, GRACE_END, SUNITA, approved_box, run

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("delivery")]

IN_GRACE = datetime(2026, 10, 12, 16, 0, tzinfo=UTC)  # Mon 21:30 IST
AFTER_GRACE = datetime(2026, 10, 12, 17, 30, tzinfo=UTC)  # Mon 23:00 IST


def key() -> dict[str, str]:
    return {"Idempotency-Key": str(uuid.uuid4())}


async def pay(api: httpx2.AsyncClient, auth: dict, order_id: uuid.UUID, **headers: str):
    return await api.post(f"/v1/orders/{order_id}/pay", headers={**auth, **key(), **headers})


async def debits(db: AsyncSession, order_id: uuid.UUID) -> int:
    found = await db.execute(
        select(func.count())
        .select_from(JournalEntry)
        .where(JournalEntry.idempotency_key == f"box:{order_id}")
    )
    return found.scalar_one()


async def locked_events(db: AsyncSession, order_id: uuid.UUID) -> int:
    rows = await db.execute(select(OutboxEvent).where(OutboxEvent.event_type == "orders.locked"))
    return sum(1 for e in rows.scalars() if e.payload["order_id"] == str(order_id))


async def topped_up(db: AsyncSession, user_id: uuid.UUID, amount: int) -> None:
    """A captured top-up and its retry of the failed debit (the outbox consumer)."""
    await credit_topup(db, user_id, amount, payment_ref=uuid.uuid4().hex, topup_id=uuid.uuid4())
    await on_wallet_topped_up(
        db, OutboxEvent(event_type="billing.wallet_topped_up", payload={"user_id": str(user_id)})
    )
    await db.commit()


@time_machine.travel(NOW, tick=False)
async def test_pay_debits_once_and_locks_a_failed_box_during_the_grace(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, priya_id = await as_user(api, google, PRIYA)
    meena, meena_id = await as_user(api, google, MEENA)
    sunita, _ = await as_user(api, google, SUNITA)
    priya_cycle, priya_order, total = await approved_box(api, priya)
    _, meena_order, meena_total = await approved_box(api, meena)
    _, sunita_order, _ = await approved_box(api, sunita)
    await credit_topup(db_session, priya_id, 1_000, payment_ref="p1", topup_id=uuid.uuid4())
    await db_session.commit()

    # Before the cut-off nothing is due yet.
    early = await pay(api, priya, priya_order)
    assert (early.status_code, early.json()["code"]) == (409, "orders.not_payable")

    with time_machine.travel(datetime(2026, 10, 12, 15, 30, tzinfo=UTC), tick=False):
        summary = await run(api, "cutoff_lock", CUTOFF_AT)
    assert summary["payment_failed"] == 3
    await relay(db_session)

    with time_machine.travel(IN_GRACE, tick=False):
        priya, _ = await as_user(api, google, PRIYA)
        meena, _ = await as_user(api, google, MEENA)
        sunita, _ = await as_user(api, google, SUNITA)

        # The week shows what is due, with the wallet read live.
        week = (await api.get(f"/v1/cycles/{priya_cycle}", headers=priya)).json()
        assert week["status"] == "payment_failed"
        assert week["payment_due"] == {
            "order_id": str(priya_order),
            "total_paise": total,
            "wallet_paise": 1_000,
            "shortfall_paise": total - 1_000,
            "due_at": "2026-10-12T17:30:00Z",
        }
        await db_session.commit()

        # Still short: refused, nothing moves; someone else's order is not found.
        short = await pay(api, priya, priya_order)
        assert (short.status_code, short.json()["code"]) == (409, "billing.insufficient_balance")
        assert (short.json()["balance_paise"], short.json()["needed_paise"]) == (1_000, total)
        stranger = await pay(api, meena, priya_order)
        assert (stranger.status_code, stranger.json()["code"]) == (404, "orders.not_found")
        assert (await orders_api.get(db_session, priya_order)).status == "payment_failed"

        # After a top-up the app pays: locked by the customer, one debit, orders.locked once.
        await credit_topup(db_session, priya_id, total, payment_ref="p2", topup_id=uuid.uuid4())
        await db_session.commit()
        same = key()
        paid = await api.post(f"/v1/orders/{priya_order}/pay", headers={**priya, **same})
        replay = await api.post(f"/v1/orders/{priya_order}/pay", headers={**priya, **same})
        again = await pay(api, priya, priya_order)
        assert paid.status_code == 200, paid.text
        body = paid.json()
        assert body["status"] == "locked" and body["locked_at"] is not None
        assert (body["timeline"][-1]["status"], body["timeline"][-1]["actor"]) == (
            "locked",
            "customer",
        )
        assert replay.headers["Idempotent-Replayed"] == "true" and replay.json() == body
        assert again.status_code == 200 and again.json()["version"] == body["version"]
        assert await debits(db_session, priya_order) == 1
        assert await billing_api.balance(db_session, priya_id) == 1_000
        assert await locked_events(db_session, priya_order) == 1

        # The top-up's retry got to Meena's box first: pay answers 200 and debits nothing more.
        await topped_up(db_session, meena_id, meena_total)
        after_topup = await pay(api, meena, meena_order)
        assert (after_topup.status_code, after_topup.json()["status"]) == (200, "locked")
        assert after_topup.json()["timeline"][-1]["actor"] == "system"
        assert await debits(db_session, meena_order) == 1
        assert await billing_api.balance(db_session, meena_id) == 0

        await relay(db_session)
        week = (await api.get(f"/v1/cycles/{priya_cycle}", headers=priya)).json()
        assert (week["status"], week["payment_due"]) == ("locked", None)
        await db_session.commit()

    # Sunita's grace is over: refused, and a late top-up's retry leaves the box to the grace job.
    with time_machine.travel(AFTER_GRACE, tick=False):
        sunita, sunita_id = await as_user(api, google, SUNITA)
        late = await pay(api, sunita, sunita_order)
        assert (late.status_code, late.json()["code"]) == (409, "orders.payment_grace_over")
        await topped_up(db_session, sunita_id, 100_000)
        assert (await orders_api.get(db_session, sunita_order)).status == "payment_failed"
        await run(api, "payment_grace_expiry", GRACE_END)
        cancelled = await pay(api, sunita, sunita_order)
        assert (cancelled.status_code, cancelled.json()["code"]) == (409, "orders.not_payable")
        assert await debits(db_session, sunita_order) == 0


@time_machine.travel(NOW, tick=False)
async def test_a_failed_week_can_be_skipped_until_its_grace_ends(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, _ = await as_user(api, google, PRIYA)
    meena, _ = await as_user(api, google, MEENA)
    priya_cycle, priya_order, _ = await approved_box(api, priya)
    meena_cycle, _, _ = await approved_box(api, meena)
    with time_machine.travel(datetime(2026, 10, 12, 15, 30, tzinfo=UTC), tick=False):
        await run(api, "cutoff_lock", CUTOFF_AT)
    await relay(db_session)

    with time_machine.travel(IN_GRACE, tick=False):
        priya, _ = await as_user(api, google, PRIYA)
        skipped = await api.post(f"/v1/cycles/{priya_cycle}/skip", headers={**priya, **key()})
        assert skipped.status_code == 200, skipped.text
        assert (skipped.json()["status"], skipped.json()["payment_due"]) == ("skipped", None)
        assert (await orders_api.get(db_session, priya_order)).status == "cancelled"
        await db_session.commit()
        # The cut-off has passed, so the week cannot come back.
        back = await api.post(
            f"/v1/cycles/{priya_cycle}/unskip",
            headers={**priya, **key(), "If-Match": f'"{skipped.json()["version"]}"'},
        )
        assert (back.status_code, back.json()["code"]) == (409, "subscriptions.cutoff_passed")

    with time_machine.travel(AFTER_GRACE, tick=False):
        meena, _ = await as_user(api, google, MEENA)
        late = await api.post(f"/v1/cycles/{meena_cycle}/skip", headers={**meena, **key()})
        assert (late.status_code, late.json()["code"]) == (409, "subscriptions.cutoff_passed")
