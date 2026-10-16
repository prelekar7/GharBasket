"""The pre-live delivery fee on box orders (M9 §3.6 and its M4 follow-up in §11): set at
approval from the hub's fee while the society has no live_at, debited with the food at the
cut-off, dropped if the society goes live first, and never touched by shortage or issue refunds."""

import uuid
from datetime import UTC, datetime, time

import httpx2
import pytest
import time_machine
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.billing.models import JournalEntry
from gharbasket.modules.billing.wallet import credit_topup
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.orders import service
from gharbasket.platform.errors import ProblemError
from gharbasket.tools.seed import SEED_HUB_ID, seed_hubs

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .test_issue_triage import NEHA
from .test_issues import DISPATCHED, placed_order, report
from .test_issues import NOW as DELIVERY_DAY
from .test_orders import NOW, PRIYA, SUNITA, approved_cycle, as_user

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("world", "hub")]

FEE = 3_000  # the seed hub's default: ₹30 a box
CUTOFF = datetime(2026, 10, 12, 15, 30, tzinfo=UTC)  # Mon 21:00 IST for Tuesday's boxes


@pytest.fixture
async def hub(world: None, db_session: AsyncSession) -> None:
    """The seed hub (with its default pre-live fee); `world` alone has none."""
    await seed_hubs(db_session)
    await db_session.commit()


async def box(db: AsyncSession, user_id: uuid.UUID, *slugs: str) -> orders_api.Order:
    """An approved box for the user's household (Priya: a live society; Sunita: unlocking)."""
    household = await identity_api.household_for_user(db, user_id)
    assert household is not None
    slot = await service.create_slot(
        db,
        hub_id=SEED_HUB_ID,
        label=f"Fees {uuid.uuid4().hex[:8]}",
        kind="box",
        start=time(7),
        end=time(9),
        capacity=10,
    )
    cycle = await approved_cycle(db, household, slot.id, *(slugs or ("tomato", "bhindi")))
    order = await orders_api.create_from_cycle(db, cycle)
    await db.commit()
    return order


async def lock(api: httpx2.AsyncClient) -> dict:
    with time_machine.travel(CUTOFF, tick=False):
        answer = await api.post(
            "/v1/internal/jobs/cutoff_lock", json={"scheduled_for": CUTOFF.isoformat()}
        )
    assert answer.status_code == 200, answer.text
    return answer.json()["summary"]


async def debits(db: AsyncSession, order_id: uuid.UUID) -> list[str]:
    rows = await db.execute(
        select(JournalEntry.idempotency_key).where(
            JournalEntry.ref_id == order_id, JournalEntry.kind == "box_debit"
        )
    )
    return list(rows.scalars())


async def set_live(db: AsyncSession, society_id: uuid.UUID, live: bool) -> None:
    await db.execute(
        text("UPDATE community.societies SET live_at = :at WHERE id = :id"),
        {"at": datetime.now(UTC) if live else None, "id": society_id},
    )
    await db.commit()


@time_machine.travel(NOW, tick=False)
async def test_a_pre_live_box_is_debited_food_and_fee_once_and_a_live_one_food_only(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, priya_id = await as_user(api, google, PRIYA)
    sunita, sunita_id = await as_user(api, google, SUNITA)
    live = await box(db_session, priya_id)
    pre_live = await box(db_session, sunita_id)

    assert (live.fees_paise, live.total_paise) == (0, live.subtotal_paise)
    assert pre_live.fees_paise == FEE
    assert pre_live.total_paise == pre_live.subtotal_paise + FEE
    assert await orders_api.box_fee(db_session, SEED_HUB_ID, pre_live.society_id) == FEE
    assert await orders_api.box_fee(db_session, SEED_HUB_ID, live.society_id) == 0
    shown = (await api.get(f"/v1/orders/{pre_live.id}", headers=sunita)).json()
    assert (shown["fees_paise"], shown["total_paise"]) == (FEE, pre_live.total_paise)
    paying = ((priya_id, live.id, live.total_paise), (sunita_id, pre_live.id, pre_live.total_paise))
    for user_id, _, total in paying:
        await credit_topup(
            db_session, user_id, total, payment_ref=uuid.uuid4().hex, topup_id=uuid.uuid4()
        )
    await db_session.commit()

    summary = await lock(api)
    again = await lock(api)

    assert (summary["locked"], again["locked"]) == (2, 0)
    db_session.expire_all()
    for user_id, order_id, _ in paying:
        assert (await orders_api.get(db_session, order_id)).status == "locked"
        assert await billing_api.balance(db_session, user_id) == 0  # food + fee, debited once
        assert len(await debits(db_session, order_id)) == 1
    paid = (await api.get(f"/v1/orders/{paying[1][1]}", headers=sunita)).json()
    assert (paid["fees_paise"], paid["total_paise"]) == (FEE, paying[1][2])
    assert (await api.get(f"/v1/orders/{paying[0][1]}", headers=priya)).json()["fees_paise"] == 0


@time_machine.travel(NOW, tick=False)
async def test_a_society_that_goes_live_before_the_cutoff_pays_no_fee(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    _, sunita_id = await as_user(api, google, SUNITA)
    order = await box(db_session, sunita_id)
    assert order.fees_paise == FEE
    order_id, version = order.id, order.version
    await credit_topup(
        db_session, sunita_id, order.total_paise, payment_ref="s1", topup_id=uuid.uuid4()
    )
    await set_live(db_session, order.society_id, live=True)

    await lock(api)

    db_session.expire_all()
    locked = await orders_api.get(db_session, order_id)
    assert (locked.status, locked.fees_paise) == ("locked", 0)
    assert locked.total_paise == locked.subtotal_paise
    assert locked.version > version + 1  # the fee change, then the lock
    assert await billing_api.balance(db_session, sunita_id) == FEE  # only the food was taken


@time_machine.travel(NOW, tick=False)
async def test_the_hubs_fee_is_frozen_at_approval_and_an_admin_changes_it_for_later_boxes(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    neha, _ = await as_user(api, google, NEHA)
    _, sunita_id = await as_user(api, google, SUNITA)
    before = await box(db_session, sunita_id, "tomato")
    hub = await api.get(f"/v1/hubs/{SEED_HUB_ID}", headers=neha)
    assert hub.json()["pre_live_box_fee_paise"] == FEE

    changed = await api.patch(
        f"/v1/admin/hubs/{SEED_HUB_ID}",
        json={"pre_live_box_fee_paise": 5_000},
        headers={**neha, "If-Match": hub.headers["ETag"]},
    )
    assert changed.status_code == 200, changed.text
    after = await box(db_session, sunita_id, "onion")

    assert (await orders_api.get(db_session, before.id)).fees_paise == FEE
    assert after.fees_paise == 5_000
    assert after.total_paise == after.subtotal_paise + 5_000


@time_machine.travel(DELIVERY_DAY, tick=False)
async def test_an_issue_credit_never_reaches_the_fee(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    sunita, sunita_id = await as_user(api, google, SUNITA)
    _, neha_id = await as_user(api, google, NEHA)
    order_id, _ = await placed_order(db_session, sunita_id, reach=DISPATCHED)
    order = await orders_api.get(db_session, order_id)
    assert order.fees_paise == FEE
    assert len(await debits(db_session, order_id)) == 1  # placed_order paid the whole total

    late = await report(api, sunita, order_id, {"kind": "late"})
    assert late.status_code == 201, late.text
    await db_session.commit()

    with pytest.raises(ProblemError) as too_much:
        await orders_api.resolve_issue(
            db_session, uuid.UUID(late.json()["id"]), "credit", order.total_paise, neha_id
        )

    assert too_much.value.code == "orders.issue_credit_invalid"
    assert too_much.value.extra == {"max_paise": order.total_paise - FEE}


@time_machine.travel(DELIVERY_DAY, tick=False)
async def test_a_shortage_refunds_the_food_and_keeps_the_fee_on_the_total(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    _, sunita_id = await as_user(api, google, SUNITA)
    order_id, ids = await placed_order(db_session, sunita_id, reach=("locked", "picking"))
    before = (await orders_api.get(db_session, order_id)).total_paise
    balance = await billing_api.balance(db_session, sunita_id)

    line = await orders_api.mark_shortage(db_session, order_id, ids["tomato"], 1)

    after = await orders_api.get(db_session, order_id)
    refund = line.unit_price_paise
    assert await billing_api.balance(db_session, sunita_id) == balance + refund
    assert after.fees_paise == FEE
    assert after.total_paise == before - refund == after.subtotal_paise + FEE
