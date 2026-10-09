"""Shortages at packing (M4 §3.1 `mark_shortage`, ADR-0026 §6): refunds to the wallet, whole-line
substitutes at the same or a lower price, one shortage per line, and the wallet cap."""

import uuid

import pytest
import time_machine
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.billing.models import JournalEntry
from gharbasket.modules.billing.wallet import CAP_PAISE
from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.orders import service
from gharbasket.modules.orders.models import OrderLine
from gharbasket.platform.errors import ProblemError

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .test_issues import NOW, assert_ledger_balances, events, placed_order
from .test_orders import PRIYA, as_user

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("world")]

# (steps, paid price per step). At the seed hub tomato (500 g, ₹25) has the substitutes
# bitter-gourd (250 g, ₹20), cucumber (500 g, ₹25) and brinjal (500 g, ₹30); potato is not one.
BOX = {"tomato": (2, 2_500), "bhindi": (2, 1_000), "onion": (2, 4_000)}
TOTAL = 15_000
PICKING = ("locked", "picking")
SHORTAGE = "orders.shortage_substituted"


async def slugs(db: AsyncSession, *names: str) -> dict[str, uuid.UUID]:
    return await catalog_api.product_ids_by_slug(db, list(names))


async def refunds(db: AsyncSession, order_id: uuid.UUID) -> list[str]:
    rows = await db.execute(
        select(JournalEntry.idempotency_key).where(
            JournalEntry.ref_id == order_id, JournalEntry.kind == "refund"
        )
    )
    return list(rows.scalars())


async def line_of(db: AsyncSession, order_id: uuid.UUID, product_id: uuid.UUID) -> OrderLine:
    return next(
        line for line in await service.lines_of(db, order_id) if line.product_id == product_id
    )


@time_machine.travel(NOW, tick=False)
async def test_a_cheaper_substitute_replaces_the_line_and_the_difference_is_refunded_once(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, priya_id = await as_user(api, google, PRIYA)
    order_id, ids = await placed_order(db_session, priya_id, reach=PICKING, box=BOX)
    gourd = (await slugs(db_session, "bitter-gourd"))["bitter-gourd"]
    before = await billing_api.balance(db_session, priya_id)
    version = (await orders_api.get(db_session, order_id)).version

    line = await orders_api.mark_shortage(db_session, order_id, ids["tomato"], 2, gourd)

    assert (line.product_id, line.substituted_from) == (gourd, ids["tomato"])
    assert (line.qty, line.shortage_qty, line.unit_price_paise) == (2, 0, 2_000)
    assert (line.step_qty, line.step_unit, line.reason_code) == (250, "g", None)
    assert await billing_api.balance(db_session, priya_id) == before + 1_000
    assert await refunds(db_session, order_id) == [f"refund:{order_id}:short_{ids['tomato'].hex}"]
    await assert_ledger_balances(db_session)
    order = await orders_api.get(db_session, order_id)
    assert (order.subtotal_paise, order.total_paise) == (TOTAL - 1_000, TOTAL - 1_000)
    assert order.version == version + 1
    assert await events(db_session, SHORTAGE) == [
        {
            "order_id": str(order_id),
            "product_id": str(ids["tomato"]),
            "shortage_qty": 2,
            "substitute_product_id": str(gourd),
            "refund_paise": 1_000,
            "total_paise": TOTAL - 1_000,
        }
    ]

    # The same call again (a retried packing scan) changes nothing and moves no money.
    again = await orders_api.mark_shortage(db_session, order_id, ids["tomato"], 2, gourd)
    assert again.product_id == gourd
    assert await billing_api.balance(db_session, priya_id) == before + 1_000
    assert len(await events(db_session, SHORTAGE)) == 1
    assert (await orders_api.get(db_session, order_id)).version == version + 1
    cucumber = (await slugs(db_session, "cucumber"))["cucumber"]
    for product_id, qty, substitute in (
        (ids["tomato"], 2, cucumber),  # another substitute for the same line
        (ids["tomato"], 1, None),
        (gourd, 1, None),  # the substitute itself
    ):
        with pytest.raises(ProblemError) as recorded:
            await orders_api.mark_shortage(db_session, order_id, product_id, qty, substitute)
        assert (recorded.value.status, recorded.value.code) == (409, "orders.shortage_recorded")
    assert await billing_api.balance(db_session, priya_id) == before + 1_000
    await db_session.commit()

    detail = (await api.get(f"/v1/orders/{order_id}", headers=priya)).json()
    swapped = next(x for x in detail["lines"] if x["product_id"] == str(gourd))
    assert swapped["slug"] == "bitter-gourd" and swapped["substituted_from"]["slug"] == "tomato"
    assert (swapped["unit_price_paise"], swapped["total_paise"]) == (2_000, 4_000)
    assert swapped["step"]["label"] == "250 g"
    assert detail["total_paise"] == TOTAL - 1_000


@time_machine.travel(NOW, tick=False)
async def test_a_same_price_substitute_refunds_nothing_and_a_partial_shortage_is_refunded(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    _, priya_id = await as_user(api, google, PRIYA)
    order_id, ids = await placed_order(db_session, priya_id, reach=PICKING, box=BOX)
    cucumber = (await slugs(db_session, "cucumber"))["cucumber"]
    before = await billing_api.balance(db_session, priya_id)

    line = await orders_api.mark_shortage(db_session, order_id, ids["tomato"], 2, cucumber)
    assert (line.product_id, line.unit_price_paise, line.step_qty) == (cucumber, 2_500, 500)
    assert await billing_api.balance(db_session, priya_id) == before
    assert await refunds(db_session, order_id) == []

    # One of two onions short: refunded at the line's paid price, never partly substituted.
    with pytest.raises(ProblemError) as partial:
        await orders_api.mark_shortage(db_session, order_id, ids["onion"], 1, ids["tomato"])
    assert partial.value.code == "orders.substitute_invalid"
    onion = await orders_api.mark_shortage(db_session, order_id, ids["onion"], 1)
    assert (onion.product_id, onion.shortage_qty, onion.substituted_from) == (ids["onion"], 1, None)
    assert await billing_api.balance(db_session, priya_id) == before + 4_000
    assert await orders_api.mark_shortage(db_session, order_id, ids["onion"], 1) is onion
    with pytest.raises(ProblemError) as more:
        await orders_api.mark_shortage(db_session, order_id, ids["onion"], 2)
    assert more.value.code == "orders.shortage_recorded"
    for qty in (0, 3):
        with pytest.raises(ProblemError) as invalid:
            await orders_api.mark_shortage(db_session, order_id, ids["bhindi"], qty)
        assert invalid.value.code == "orders.qty_invalid"
    assert await billing_api.balance(db_session, priya_id) == before + 4_000
    order = await orders_api.get(db_session, order_id)
    assert order.total_paise == TOTAL - 4_000
    assert [e["refund_paise"] for e in await events(db_session, SHORTAGE)] == [0, 4_000]
    await assert_ledger_balances(db_session)


@time_machine.travel(NOW, tick=False)
async def test_a_substitute_must_be_listed_cheaper_and_new_to_the_order_while_packing(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    _, priya_id = await as_user(api, google, PRIYA)
    order_id, ids = await placed_order(db_session, priya_id, reach=PICKING, box=BOX)
    other = await slugs(db_session, "brinjal", "potato")
    before = await billing_api.balance(db_session, priya_id)

    for substitute, code, reason in (
        (ids["bhindi"], "orders.substitute_invalid", "on_order"),
        (other["brinjal"], "orders.substitute_invalid", "pricier"),
        (other["potato"], "orders.substitute_invalid", "not_a_substitute"),
        (uuid.uuid4(), "orders.product_unknown", None),
    ):
        with pytest.raises(ProblemError) as refused:
            await orders_api.mark_shortage(db_session, order_id, ids["tomato"], 2, substitute)
        assert refused.value.code == code
        assert (refused.value.extra or {}).get("reason") == reason
    with pytest.raises(ProblemError) as stranger:
        await orders_api.mark_shortage(db_session, order_id, other["potato"], 1)
    assert stranger.value.code == "orders.not_found"
    tomato = await line_of(db_session, order_id, ids["tomato"])
    assert (tomato.product_id, tomato.shortage_qty, tomato.substituted_from) == (
        ids["tomato"],
        0,
        None,
    )
    assert await billing_api.balance(db_session, priya_id) == before
    assert await events(db_session, SHORTAGE) == []

    # Once the bags have left the hub, a shortage is an issue for the customer to report.
    delivered, delivered_ids = await placed_order(db_session, priya_id, box=BOX)
    with pytest.raises(ProblemError) as late:
        await orders_api.mark_shortage(db_session, delivered, delivered_ids["onion"], 1)
    assert (late.value.status, late.value.code) == (409, "orders.not_editable")


@time_machine.travel(NOW, tick=False)
async def test_a_full_wallet_refuses_the_refund_and_nothing_is_recorded(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    _, priya_id = await as_user(api, google, PRIYA)
    order_id, ids = await placed_order(
        db_session, priya_id, reach=PICKING, box=BOX, extra_topup=CAP_PAISE
    )
    assert await billing_api.balance(db_session, priya_id) == CAP_PAISE

    with pytest.raises(ProblemError) as full:
        await orders_api.mark_shortage(db_session, order_id, ids["onion"], 1)

    assert (full.value.code, full.value.extra) == ("billing.wallet_limit", {"room_paise": 0})
    onion = await line_of(db_session, order_id, ids["onion"])
    assert onion.shortage_qty == 0
    assert (await orders_api.get(db_session, order_id)).total_paise == TOTAL
    assert await billing_api.balance(db_session, priya_id) == CAP_PAISE
    assert await refunds(db_session, order_id) == [] and await events(db_session, SHORTAGE) == []
    # A substitute at the same price needs no refund, so the cap does not stop it.
    cucumber = (await slugs(db_session, "cucumber"))["cucumber"]
    line = await orders_api.mark_shortage(db_session, order_id, ids["tomato"], 2, cucumber)
    assert line.product_id == cucumber
    await assert_ledger_balances(db_session)


@time_machine.travel(NOW, tick=False)
async def test_a_household_that_chose_a_refund_is_never_substituted(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, priya_id = await as_user(api, google, PRIYA)
    order_id, ids = await placed_order(db_session, priya_id, reach=PICKING, box=BOX)
    gourd = (await slugs(db_session, "bitter-gourd"))["bitter-gourd"]
    before = await billing_api.balance(db_session, priya_id)
    # Chosen after the box was locked: packing reads it when the shortage is recorded (M1 §3.4).
    me = await api.get("/v1/households/me", headers=priya)
    chosen = await api.patch(
        f"/v1/households/{me.json()['id']}",
        json={"shortage_preference": "refund"},
        headers={**priya, "If-Match": me.headers["etag"]},
    )
    assert chosen.status_code == 200, chosen.text

    with pytest.raises(ProblemError) as refused:
        await orders_api.mark_shortage(db_session, order_id, ids["tomato"], 2, gourd)

    assert (refused.value.status, refused.value.code) == (422, "orders.substitute_invalid")
    assert refused.value.extra == {"reason": "refund_preferred"}
    tomato = await line_of(db_session, order_id, ids["tomato"])
    assert (tomato.shortage_qty, tomato.substituted_from) == (0, None)
    assert await billing_api.balance(db_session, priya_id) == before
    assert await events(db_session, SHORTAGE) == []
    # The shortage itself is still recorded, as a refund.
    line = await orders_api.mark_shortage(db_session, order_id, ids["tomato"], 2)
    assert (line.product_id, line.shortage_qty) == (ids["tomato"], 2)
    assert await billing_api.balance(db_session, priya_id) == before + 5_000
    await assert_ledger_balances(db_session)
