"""Kept items and returned bags (M6 §3.5, ADR-0027 §2, M4 §3.8): a bag back after a second miss
splits into the household's kept items and grade C returned lots, once; a lost item is refunded by
the hub manager; M4's expiry releases the items into lots; the erase hook does the same."""

import uuid
from datetime import date, datetime, timedelta
from typing import Any

import httpx2
import pytest
import time_machine
from sqlalchemy import event, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.billing.wallet import CAP_PAISE
from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.hub_ops import api as hub_ops_api
from gharbasket.modules.hub_ops import jobs as hub_jobs
from gharbasket.modules.hub_ops.models import InventoryLot, KeptItem, StockMove
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.orders.models import CarryOver
from gharbasket.platform.models import AuditEntry, OutboxEvent
from gharbasket.platform.outbox import relay
from gharbasket.tools.seed import SEED_HUB_ID

from ..billing.paid import order_refund_to_source, paid_topup
from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from ..orders.test_carry_over import LOCKED, at, events, job, miss_twice, place
from ..orders.test_shortages import refunds
from .conftest import KAVITA, PRIYA, SANJAY, VIKRAM, key
from .test_packer_actions import act
from .test_packing import NOW, TASKS, build, locked_order, staff, user_id

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("floor")]

HUB = f"/v1/hubs/{SEED_HUB_ID}"
# (steps, paid price per step): bhindi keeps 72 h, the two dals months
BOX = {"bhindi": (2, 2_000), "toor-dal": (2, 9_000), "moong-dal": (1, 7_000)}


def qr(order_id: uuid.UUID) -> str:
    return f"GB1:{order_id.hex}"


async def missed_bag(
    api: httpx2.AsyncClient,
    google: FakeGoogle,
    db: AsyncSession,
    box: dict[str, tuple[int, int]] = BOX,
) -> tuple[uuid.UUID, dict[str, uuid.UUID], dict[str, Any]]:
    """PRIYA's box packed by KAVITA and missed twice: refunded, its non-perishables carried by
    M4. Returns the order, its products and the packed task."""
    manager, _ = await staff(api, google, VIKRAM)
    order_id, ids = await locked_order(db, PRIYA, box)
    await build(api, manager)
    packer, _ = await staff(api, google, KAVITA)
    task = (await api.post(f"{TASKS}/next", headers={**packer, **key()})).json()
    for slug, (qty, _) in box.items():
        picked = await act(
            api, packer, task["id"], "pick", {"product_id": str(ids[slug]), "picked_qty": qty}
        )
        assert picked.status_code == 200, picked.text
    done = await act(api, packer, task["id"], "complete")
    assert done.status_code == 200, done.text
    for to in ("dispatched", "at_society", "undelivered"):
        await orders_api.transition(db, order_id, to, actor="hub")
    await miss_twice(db, order_id)
    return order_id, ids, done.json()


async def scan(
    api: httpx2.AsyncClient, auth: dict[str, str], code: str, idem: str | None = None
) -> httpx2.Response:
    return await api.post(
        f"{HUB}/returned-bags",
        json={"bag_qr": code},
        headers={**auth, "Idempotency-Key": idem or uuid.uuid4().hex},
    )


@time_machine.travel(NOW, tick=False)
async def test_a_returned_bag_splits_into_kept_items_and_returned_lots_once(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    order_id, ids, task = await missed_bag(api, google, db_session)
    packer, _ = await staff(api, google, KAVITA)

    first = await scan(api, packer, qr(order_id), "bag-1")

    assert first.status_code == 200, first.text
    body = first.json()
    assert body["order_id"] == str(order_id)
    assert body["society"]["name"] == "Sunrise Towers" and body["flat_label"].endswith("A-1203")
    kept = {k["slug"]: k for k in body["kept"]}
    assert set(kept) == {"toor-dal", "moong-dal"}
    assert (kept["toor-dal"]["qty"], kept["toor-dal"]["status"]) == (2, "kept")
    assert kept["toor-dal"]["step"] == {"qty": 500, "unit": "g", "label": "500 g"}
    assert kept["moong-dal"]["qty"] == 1
    assert kept["toor-dal"]["flat_label"] == body["flat_label"]
    assert kept["toor-dal"]["packed_task_id"] is None and kept["toor-dal"]["settled_at"] is None
    assert {k["from_order_id"] for k in kept.values()} == {str(order_id)}
    rows = {
        r.product_id: r
        for r in await db_session.scalars(
            select(CarryOver).where(CarryOver.from_order_id == order_id)
        )
    }
    assert {k["carry_id"] for k in kept.values()} == {str(r.id) for r in rows.values()}

    # The rest of the bag: bhindi (perishable) becomes one grade C lot, no cost, best before the
    # task's packing time plus the product's shelf life.
    [lot] = body["lots"]
    packed_at = datetime.fromisoformat(task["packed_at"])
    assert (lot["slug"], lot["source"], lot["grade"], lot["unit_cost_paise"]) == (
        "bhindi",
        "returned",
        "C",
        None,
    )
    bhindi = (await catalog_api.stock_products(db_session, [ids["bhindi"]]))[ids["bhindi"]]
    assert bhindi.shelf_life_h == 72
    assert datetime.fromisoformat(lot["best_before"]) == packed_at + timedelta(hours=72)
    assert lot["qty_in"] == lot["qty_on_hand"] == 2 * bhindi.step_qty / 1000
    assert lot["unit"] == "kg"
    move = await db_session.scalar(
        select(StockMove).where(StockMove.lot_id == uuid.UUID(lot["id"]))
    )
    assert move is not None and (move.kind, move.ref_id) == ("return", order_id)
    stocked = await db_session.scalar(select(func.count()).select_from(InventoryLot))
    assert stocked == 1  # the dals are on the shelf for the household, not stock

    # A second scan of the same bag, under the same key or a new one, answers the same.
    for again in (
        await scan(api, packer, qr(order_id), "bag-1"),
        await scan(api, packer, qr(order_id)),
    ):
        assert again.status_code == 200, again.text
        assert again.json() == body
    assert await db_session.scalar(select(func.count()).select_from(InventoryLot)) == 1
    assert await db_session.scalar(select(func.count()).select_from(KeptItem)) == 2

    # The shelf list, by society and flat, for hub staff and the franchise owner.
    manager, _ = await staff(api, google, VIKRAM)
    listed = await api.get(f"{HUB}/kept-items", params={"status": "kept"}, headers=manager)
    assert listed.status_code == 200, listed.text
    assert {k["id"] for k in listed.json()["items"]} == {k["id"] for k in kept.values()}
    none = await api.get(f"{HUB}/kept-items", params={"status": "packed"}, headers=manager)
    assert none.json() == {"items": []}
    owner, _ = await staff(api, google, SANJAY)
    assert (await api.get(f"{HUB}/kept-items", headers=owner)).status_code == 200
    customer, _ = await staff(api, google, "+919800001002")
    assert (await api.get(f"{HUB}/kept-items", headers=customer)).status_code == 403
    assert (await scan(api, customer, qr(order_id))).status_code == 403


@time_machine.travel(NOW, tick=False)
async def test_a_scan_of_anything_but_a_bag_back_after_a_second_miss_is_refused(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    manager, _ = await staff(api, google, VIKRAM)
    order_id, ids = await locked_order(db_session, PRIYA, BOX)
    await build(api, manager)
    packer, _ = await staff(api, google, KAVITA)

    for code in ("nonsense", f"GB1:{uuid.uuid4().hex}", "GB1:xyz", qr(order_id)):
        refused = await scan(api, packer, code)  # no task started: queued, not packed
        assert (refused.status_code, refused.json()["code"]) == (404, "hub_ops.bag_unknown")

    task = (await api.post(f"{TASKS}/next", headers={**packer, **key()})).json()
    for slug, (qty, _) in BOX.items():
        await act(
            api, packer, task["id"], "pick", {"product_id": str(ids[slug]), "picked_qty": qty}
        )
    await act(api, packer, task["id"], "complete")
    # Packed, not missed: nothing is unpacked, and the answer names the order's status.
    now = "packed"
    for to in ("dispatched", "at_society", "undelivered", "redelivery"):
        refused = await scan(api, packer, qr(order_id))
        assert (refused.status_code, refused.json()["code"]) == (409, "hub_ops.bag_not_returned")
        assert refused.json()["current_status"] == now
        await orders_api.transition(
            db_session, order_id, to, actor="system" if to == "redelivery" else "hub"
        )
        await db_session.commit()  # a refused request rolls the shared session back
        now = to
    assert await db_session.scalar(select(func.count()).select_from(InventoryLot)) == 0


@time_machine.travel(NOW, tick=False)
async def test_a_lost_kept_item_is_refunded_once_and_audited(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    order_id, ids, _ = await missed_bag(api, google, db_session)
    packer, _ = await staff(api, google, KAVITA)
    kept = {k["slug"]: k for k in (await scan(api, packer, qr(order_id))).json()["kept"]}
    manager, vikram = await staff(api, google, VIKRAM)
    owner = await user_id(db_session, PRIYA)
    balance = await billing_api.balance(db_session, owner)
    path = f"{HUB}/kept-items/{kept['toor-dal']['id']}/lost"

    forbidden = await api.post(path, json={"note": "Gone"}, headers={**packer, **key()})
    assert forbidden.status_code == 403
    blank = await api.post(path, json={"note": "  "}, headers={**manager, **key()})
    assert (blank.status_code, blank.json()["code"]) == (422, "request.invalid")

    lost = await api.post(
        path,
        json={"note": "Not on the shelf at the count"},
        headers={**manager, "Idempotency-Key": "lost-1"},
    )

    assert lost.status_code == 200, lost.text
    assert (lost.json()["status"], lost.json()["id"]) == ("lost", kept["toor-dal"]["id"])
    assert lost.json()["settled_at"] is not None
    assert await billing_api.balance(db_session, owner) == balance + 18_000  # 2 steps at ₹90
    assert f"refund:{order_id}:carry_{ids['toor-dal'].hex}" in await refunds(db_session, order_id)
    [entry] = (
        await db_session.scalars(select(AuditEntry).where(AuditEntry.action == "kept_items.lost"))
    ).all()
    assert (entry.actor_user_id, entry.entity_id) == (vikram, kept["toor-dal"]["id"])
    assert entry.after is not None and entry.after["note"] == "Not on the shelf at the count"
    carry = await db_session.scalar(
        select(CarryOver).where(CarryOver.id == uuid.UUID(kept["toor-dal"]["carry_id"]))
    )
    assert carry is not None and (carry.status, carry.refunded_by) == ("refunded", "hub")

    # The same request again, or the item reported lost again, changes nothing.
    same = await api.post(
        path,
        json={"note": "Not on the shelf at the count"},
        headers={**manager, "Idempotency-Key": "lost-1"},
    )
    again = await api.post(path, json={"note": "Again"}, headers={**manager, **key()})
    assert same.json() == again.json() == lost.json()
    assert await billing_api.balance(db_session, owner) == balance + 18_000
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(AuditEntry)
            .where(AuditEntry.action == "kept_items.lost")
        )
        == 1
    )

    # A packed or released item is settled; another hub's or unknown one is not found.
    moong = f"{HUB}/kept-items/{kept['moong-dal']['id']}/lost"
    await db_session.execute(
        update(KeptItem)
        .where(KeptItem.id == uuid.UUID(kept["moong-dal"]["id"]))
        .values(status="released")
    )
    await db_session.commit()
    settled = await api.post(moong, json={"note": "Gone"}, headers={**manager, **key()})
    assert (settled.status_code, settled.json()["code"]) == (409, "hub_ops.kept_item_settled")
    assert settled.json()["current_status"] == "released"
    unknown = await api.post(
        f"{HUB}/kept-items/{uuid.uuid4()}/lost", json={"note": "Gone"}, headers={**manager, **key()}
    )
    assert (unknown.status_code, unknown.json()["code"]) == (404, "hub_ops.kept_item_not_found")


@time_machine.travel(NOW, tick=False)
async def test_a_lost_kept_item_is_refunded_to_the_payment_source_when_the_wallet_is_full(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    order_id, ids, _ = await missed_bag(api, google, db_session)
    packer, _ = await staff(api, google, KAVITA)
    kept = {k["slug"]: k for k in (await scan(api, packer, qr(order_id))).json()["kept"]}
    manager, _ = await staff(api, google, VIKRAM)
    owner = await user_id(db_session, PRIYA)
    await paid_topup(db_session, owner, CAP_PAISE - await billing_api.balance(db_session, owner))
    await db_session.commit()

    lost = await api.post(
        f"{HUB}/kept-items/{kept['toor-dal']['id']}/lost",
        json={"note": "Not on the shelf at the count"},
        headers={**manager, **key()},
    )

    assert lost.status_code == 200, lost.text
    assert lost.json()["status"] == "lost"
    assert await billing_api.balance(db_session, owner) == CAP_PAISE  # sent back, not kept
    assert f"refund:{order_id}:carry_{ids['toor-dal'].hex}" in await refunds(db_session, order_id)
    await order_refund_to_source(db_session, order_id, 18_000, f"carry_{ids['toor-dal'].hex}")


@time_machine.travel(NOW, tick=False)
async def test_an_item_can_be_reported_lost_while_its_box_is_picked_but_not_once_packed(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    order_id, ids, _ = await missed_bag(api, google, db_session)
    packer, _ = await staff(api, google, KAVITA)
    kept = {k["slug"]: k for k in (await scan(api, packer, qr(order_id))).json()["kept"]}
    owner = await user_id(db_session, PRIYA)
    # M4 attaches the items to the household's next box, which is then being picked.
    next_box = await place(db_session, owner, date(2026, 10, 14), LOCKED)
    await orders_api.transition(db_session, next_box, "picking", actor="packer")
    await db_session.commit()
    manager, _ = await staff(api, google, VIKRAM)
    balance = await billing_api.balance(db_session, owner)

    lost = await api.post(
        f"{HUB}/kept-items/{kept['toor-dal']['id']}/lost",
        json={"note": "Gone"},
        headers={**manager, **key()},
    )

    assert lost.status_code == 200, lost.text
    assert lost.json()["status"] == "lost"
    assert await billing_api.balance(db_session, owner) == balance + 18_000  # refunded once
    assert f"refund:{order_id}:carry_{ids['toor-dal'].hex}" in await refunds(db_session, order_id)
    again = await api.post(
        f"{HUB}/kept-items/{kept['toor-dal']['id']}/lost",
        json={"note": "Gone"},
        headers={**manager, **key()},
    )
    assert again.json() == lost.json()
    assert await billing_api.balance(db_session, owner) == balance + 18_000

    # Once the box is packed the other item is on its way: the hub can no longer refund it.
    await orders_api.transition(db_session, next_box, "packed", actor="packer")
    await db_session.commit()
    refused = await api.post(
        f"{HUB}/kept-items/{kept['moong-dal']['id']}/lost",
        json={"note": "Gone"},
        headers={**manager, **key()},
    )
    assert (refused.status_code, refused.json()["code"]) == (409, "orders.carry_not_refundable")
    assert refused.json()["current_status"] == "packed"
    item = await db_session.scalar(
        select(KeptItem)
        .where(KeptItem.id == uuid.UUID(kept["moong-dal"]["id"]))
        .execution_options(populate_existing=True)
    )
    assert item is not None and item.status == "kept"  # nothing recorded


@time_machine.travel(NOW, tick=False)
async def test_reporting_an_item_lost_locks_the_task_then_the_item_then_the_carry_row(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    """The order `packing.complete` takes too (task, carry rows, kept item): no deadlock."""
    api = clients()
    order_id, _, _ = await missed_bag(api, google, db_session)
    packer, _ = await staff(api, google, KAVITA)
    kept = (await scan(api, packer, qr(order_id))).json()["kept"][0]
    owner = await user_id(db_session, PRIYA)
    await place(db_session, owner, date(2026, 10, 14), LOCKED)
    manager, _ = await staff(api, google, VIKRAM)
    locks: list[str] = []

    def record(_conn: Any, _cursor: Any, statement: str, *_: Any) -> None:
        if "FOR UPDATE" in statement and statement.lstrip().upper().startswith("SELECT"):
            for table in ("packing_tasks", "kept_items", "carry_overs"):
                if f"FROM {'hub_ops' if table != 'carry_overs' else 'orders'}.{table}" in statement:
                    locks.append(table)

    assert db_session.bind is not None
    engine = db_session.bind.sync_engine  # type: ignore[union-attr]
    event.listen(engine, "before_cursor_execute", record)
    try:
        lost = await api.post(
            f"{HUB}/kept-items/{kept['id']}/lost",
            json={"note": "Gone"},
            headers={**manager, **key()},
        )
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert lost.status_code == 200, lost.text
    first = {table: locks.index(table) for table in dict.fromkeys(locks)}
    assert first["packing_tasks"] < first["kept_items"] < first["carry_overs"], locks


@time_machine.travel(NOW, tick=False)
async def test_an_item_whose_row_moved_to_another_box_meanwhile_is_a_409_to_retry(
    clients: ClientFactory,
    google: FakeGoogle,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = clients()
    order_id, _, _ = await missed_bag(api, google, db_session)
    packer, _ = await staff(api, google, KAVITA)
    kept = (await scan(api, packer, qr(order_id))).json()["kept"][0]
    manager, _ = await staff(api, google, VIKRAM)
    real = orders_api.carry_target
    seen = {"n": 0}

    async def moving(session: AsyncSession, carry_id: uuid.UUID) -> uuid.UUID | None:
        seen["n"] += 1
        return await real(session, carry_id) if seen["n"] == 1 else uuid.uuid4()

    monkeypatch.setattr(orders_api, "carry_target", moving)

    refused = await api.post(
        f"{HUB}/kept-items/{kept['id']}/lost", json={"note": "Gone"}, headers={**manager, **key()}
    )

    assert (refused.status_code, refused.json()["code"]) == (409, "hub_ops.kept_item_moved")
    item = await db_session.scalar(
        select(KeptItem)
        .where(KeptItem.id == uuid.UUID(kept["id"]))
        .execution_options(populate_existing=True)
    )
    assert item is not None and item.status == "kept"


@time_machine.travel(NOW, tick=False)
async def test_the_expiry_of_the_carry_over_releases_the_kept_items_into_returned_lots(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    order_id, ids, _ = await missed_bag(api, google, db_session)
    packer, _ = await staff(api, google, KAVITA)
    kept = {k["slug"]: k for k in (await scan(api, packer, qr(order_id))).json()["kept"]}
    manager, _ = await staff(api, google, VIKRAM)
    lost = await api.post(
        f"{HUB}/kept-items/{kept['moong-dal']['id']}/lost",
        json={"note": "Gone"},
        headers={**manager, **key()},
    )
    assert lost.status_code == 200, lost.text

    # 14 days after the miss M4 refunds what is still waiting; M6 hears it from the outbox.
    day = datetime(2026, 10, 28).date()
    assert (await job(db_session, "expire_carry_overs", at(day, 2, 40)))["refunded"] == 1
    [event] = await events(db_session, "orders.carry_over_expired")
    assert [line["carry_id"] for line in event["lines"]] == [kept["toor-dal"]["carry_id"]]
    await relay(db_session)

    db_session.expire_all()
    items = {i.id: i for i in await db_session.scalars(select(KeptItem).order_by(KeptItem.id))}
    toor = items[uuid.UUID(kept["toor-dal"]["id"])]
    assert toor.status == "released" and toor.settled_at is not None
    assert items[uuid.UUID(kept["moong-dal"]["id"])].status == "lost"  # left alone
    lots = list(
        await db_session.scalars(
            select(InventoryLot).where(
                InventoryLot.product_id == ids["toor-dal"], InventoryLot.source == "returned"
            )
        )
    )
    [lot] = lots
    assert (lot.grade, lot.unit, lot.qty_on_hand, lot.unit_cost_paise) == ("C", "kg", 1, None)
    assert lot.order_id == order_id
    move = await db_session.scalar(select(StockMove).where(StockMove.lot_id == lot.id))
    assert move is not None and (move.kind, move.kept_item_id) == ("return", toor.id)

    # A replay of the event, twice, changes nothing.
    for _ in range(2):
        await hub_jobs.on_carry_over_expired(
            db_session, OutboxEvent(event_type="orders.carry_over_expired", payload=event)
        )
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(InventoryLot)
            .where(InventoryLot.product_id == ids["toor-dal"])
        )
        == 1
    )


@time_machine.travel(NOW, tick=False)
async def test_erasing_the_household_releases_its_kept_items_and_forgets_the_flat(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    order_id, _, _ = await missed_bag(api, google, db_session)
    packer, _ = await staff(api, google, KAVITA)
    assert (await scan(api, packer, qr(order_id))).status_code == 200
    owner = await user_id(db_session, PRIYA)
    household = await identity_api.household_for_user(db_session, owner)
    assert household is not None

    counts = await hub_ops_api.anonymise_for_user(db_session, owner, household.id)

    assert counts["kept_items"] == 4  # two released, two scrubbed of their flat
    items = list(await db_session.scalars(select(KeptItem)))
    assert {i.status for i in items} == {"released"}
    assert all(i.flat is None and i.tower_id is None for i in items)
    assert (
        await db_session.scalar(
            select(func.count()).select_from(InventoryLot).where(InventoryLot.source == "returned")
        )
        == 3
    )  # bhindi from the bag, and the two dals
    again = await hub_ops_api.anonymise_for_user(db_session, owner, household.id)
    assert again["kept_items"] == 0
