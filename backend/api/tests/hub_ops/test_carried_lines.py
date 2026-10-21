"""Carried items on a packing task (M6 §3.2, §3.5, M4 §3.8): the kept items M4 attached to a box
are lines of their own, picked from the shelf and apart from a paid line of the same product; they
take no shortage and no part in procurement; packing the bag marks the kept item packed."""

import uuid
from datetime import date
from typing import Any

import httpx2
import pytest
import time_machine
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.hub_ops.models import KeptItem, StockMove
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.orders.models import CarryOver

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from ..orders.test_carry_over import LOCKED, miss_twice, place
from .conftest import KAVITA, PRIYA, VIKRAM, key
from .test_kept_items import HUB, missed_bag, qr, scan
from .test_packer_actions import act
from .test_packing import NOW, TASKS, staff, user_id

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("floor")]

NEXT = date(2026, 10, 14)  # the box the kept items ride on; the miss was on the 13th
# The next box has a paid line of one of the kept products and none of the other.
NEXT_BOX = {"bhindi": (1, 2_000), "toor-dal": (1, 9_000)}
PLANS = f"{HUB}/procurement-plans"


async def build_next(api: httpx2.AsyncClient, manager: dict[str, str]) -> dict[str, int]:
    built = await api.post(
        f"{TASKS}/build", json={"date": NEXT.isoformat()}, headers={**manager, **key()}
    )
    assert built.status_code == 200, built.text
    return built.json()


async def next_task(api: httpx2.AsyncClient, auth: dict[str, str]) -> dict[str, Any]:
    listed = await api.get(TASKS, params={"date": NEXT.isoformat()}, headers=auth)
    [task] = listed.json()["items"]
    shown = await api.get(f"/v1/packing-tasks/{task['id']}", headers=auth)
    assert shown.status_code == 200, shown.text
    return shown.json()


def lines(task: dict[str, Any]) -> dict[tuple[str, bool], dict[str, Any]]:
    return {(x["slug"], x["carried"]): x for x in task["lines"]}


async def moves(db: AsyncSession) -> list[StockMove]:
    found = await db.execute(
        select(StockMove)
        .where(StockMove.kind == "pack")
        .order_by(StockMove.product_id)
        .execution_options(populate_existing=True)
    )
    return list(found.scalars())


@time_machine.travel(NOW, tick=False)
async def test_kept_items_are_their_own_lines_picked_from_the_shelf_and_marked_packed(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    missed, ids, _ = await missed_bag(api, google, db_session)
    packer, _ = await staff(api, google, KAVITA)
    assert (await scan(api, packer, qr(missed))).status_code == 200
    owner = await user_id(db_session, PRIYA)
    next_box = await place(db_session, owner, NEXT, LOCKED, NEXT_BOX)
    manager, _ = await staff(api, google, VIKRAM)

    assert await build_next(api, manager) == {"created": 1, "existing": 0}

    task = await next_task(api, manager)
    shown = lines(task)
    # Four lines: the two paid ones, and the kept toor dal beside the paid toor dal, and moong dal.
    assert set(shown) == {
        ("bhindi", False),
        ("toor-dal", False),
        ("toor-dal", True),
        ("moong-dal", True),
    }
    assert task["lines_total"] == 4 and task["lines_done"] == 0
    assert (shown["toor-dal", False]["qty"], shown["toor-dal", True]["qty"]) == (1, 2)
    assert shown["toor-dal", True]["step"] == {"qty": 500, "unit": "g", "label": "500 g"}
    assert shown["moong-dal", True]["qty"] == 1
    # The paid line is listed before the kept one of the same product.
    names = [(x["slug"], x["carried"]) for x in task["lines"]]
    assert names.index(("toor-dal", False)) < names.index(("toor-dal", True))

    started = await act(api, packer, task["id"], "start")
    assert started.status_code == 200, started.text
    carried_rows = await db_session.scalars(
        select(CarryOver).where(CarryOver.to_order_id == next_box)
    )
    # While the box is picked the rows are still `attached`: the hub can yet report one lost.
    assert {r.status for r in carried_rows} == {"attached"}

    # A kept item takes no shortage, with or without the flag; the paid line still can.
    for body in (
        {"product_id": str(ids["toor-dal"]), "short_qty": 1, "carried": True},
        {"product_id": str(ids["moong-dal"]), "short_qty": 1},
    ):
        refused = await act(api, packer, task["id"], "shortage", body)
        assert (refused.status_code, refused.json()["code"]) == (422, "hub_ops.qty_invalid")
        assert refused.json()["reason"] == "carried"
    out_of_range = await act(
        api,
        packer,
        task["id"],
        "pick",
        {"product_id": str(ids["toor-dal"]), "carried": True, "picked_qty": 3},
    )
    assert out_of_range.json()["code"] == "hub_ops.qty_invalid"

    # Every line, the kept ones too, is picked before the bag is packed.
    for slug, carried, qty in (("bhindi", False, 1), ("toor-dal", False, 1)):
        picked = await act(
            api,
            packer,
            task["id"],
            "pick",
            {"product_id": str(ids[slug]), "carried": carried, "picked_qty": qty},
        )
        assert picked.status_code == 200, picked.text
    open_lines = await act(api, packer, task["id"], "complete")
    assert (open_lines.status_code, open_lines.json()["code"]) == (409, "hub_ops.lines_open")
    assert set(open_lines.json()["product_ids"]) == {str(ids["toor-dal"]), str(ids["moong-dal"])}
    for body in (
        {"product_id": str(ids["toor-dal"]), "carried": True, "picked_qty": 2},
        {"product_id": str(ids["moong-dal"]), "picked_qty": 1},  # the only line of the product
    ):
        picked = await act(api, packer, task["id"], "pick", body)
        assert picked.status_code == 200, picked.text
    after = lines(picked.json())
    assert after["toor-dal", False]["picked_qty"] == 1  # the paid line kept its own count
    assert (after["toor-dal", True]["picked_qty"], after["moong-dal", True]["picked_qty"]) == (2, 1)

    done = await act(api, packer, task["id"], "complete")

    assert done.status_code == 200, done.text
    assert done.json()["status"] == "packed" and done.json()["lines_done"] == 4
    packed_rows = await db_session.scalars(
        select(CarryOver)
        .where(CarryOver.to_order_id == next_box)
        .execution_options(populate_existing=True)
    )
    assert {r.status for r in packed_rows} == {"packed"}  # M4 follows the order into packed
    listed = await api.get(f"{HUB}/kept-items", params={"status": "packed"}, headers=manager)
    packed = {k["slug"]: k for k in listed.json()["items"]}
    assert set(packed) == {"toor-dal", "moong-dal"}
    assert {k["packed_task_id"] for k in packed.values()} == {task["id"]}
    assert all(k["settled_at"] is not None for k in packed.values())
    assert (
        await api.get(f"{HUB}/kept-items", params={"status": "kept"}, headers=manager)
    ).json() == {"items": []}
    # Their pack moves (no lot), by the kept item: 2 x 500 g and 1 x 500 g.
    written = {m.product_id: m for m in await moves(db_session)}
    assert set(written) == {ids["toor-dal"], ids["moong-dal"]}
    for slug, qty in (("toor-dal", -1.0), ("moong-dal", -0.5)):
        move = written[ids[slug]]
        assert (float(move.qty), move.unit, move.lot_id) == (qty, "kg", None)
        assert move.kept_item_id == uuid.UUID(packed[slug]["id"])


@time_machine.travel(NOW, tick=False)
async def test_a_bag_never_scanned_still_packs_its_carried_items_from_the_shelf_unlotted(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    missed, ids, _ = await missed_bag(api, google, db_session)  # the bag is not scanned
    owner = await user_id(db_session, PRIYA)
    await place(db_session, owner, NEXT, LOCKED, NEXT_BOX)
    manager, _ = await staff(api, google, VIKRAM)
    packer, _ = await staff(api, google, KAVITA)
    await build_next(api, manager)
    task = await next_task(api, manager)
    assert (await act(api, packer, task["id"], "start")).status_code == 200
    for slug, carried, qty in (
        ("bhindi", False, 1),
        ("toor-dal", False, 1),
        ("toor-dal", True, 2),
        ("moong-dal", True, 1),
    ):
        picked = await act(
            api,
            packer,
            task["id"],
            "pick",
            {"product_id": str(ids[slug]), "carried": carried, "picked_qty": qty},
        )
        assert picked.status_code == 200, picked.text

    done = await act(api, packer, task["id"], "complete")

    assert done.status_code == 200, done.text
    assert await db_session.scalar(select(KeptItem.id).limit(1)) is None  # no kept item to mark
    carry = {
        r.product_id: r
        for r in await db_session.scalars(
            select(CarryOver).where(CarryOver.from_order_id == missed)
        )
    }
    written = {m.product_id: m for m in await moves(db_session)}
    for slug in ("toor-dal", "moong-dal"):
        move = written[ids[slug]]
        assert (move.lot_id, move.kept_item_id, move.ref_id) == (None, None, carry[ids[slug]].id)
        assert move.qty < 0


@time_machine.travel(NOW, tick=False)
async def test_a_kept_item_lost_before_packing_leaves_the_task(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    missed, ids, _ = await missed_bag(api, google, db_session)
    packer, _ = await staff(api, google, KAVITA)
    kept = {k["slug"]: k for k in (await scan(api, packer, qr(missed))).json()["kept"]}
    owner = await user_id(db_session, PRIYA)
    await place(db_session, owner, NEXT, LOCKED, NEXT_BOX)
    manager, _ = await staff(api, google, VIKRAM)
    await build_next(api, manager)
    before = await next_task(api, manager)
    assert ("toor-dal", True) in lines(before)

    lost = await api.post(
        f"{HUB}/kept-items/{kept['toor-dal']['id']}/lost",
        json={"note": "Not on the shelf"},
        headers={**manager, **key()},
    )

    assert lost.status_code == 200, lost.text
    task = await next_task(api, manager)
    assert set(lines(task)) == {("bhindi", False), ("toor-dal", False), ("moong-dal", True)}
    assert (task["lines_total"], task["version"]) == (3, before["version"] + 1)
    assert (await act(api, packer, task["id"], "start")).status_code == 200
    for slug, carried, qty in (
        ("bhindi", False, 1),
        ("toor-dal", False, 1),
        ("moong-dal", True, 1),
    ):
        picked = await act(
            api,
            packer,
            task["id"],
            "pick",
            {"product_id": str(ids[slug]), "carried": carried, "picked_qty": qty},
        )
        assert picked.status_code == 200, picked.text
    assert (await act(api, packer, task["id"], "complete")).status_code == 200
    statuses = {
        k.product_id: k.status
        for k in await db_session.scalars(
            select(KeptItem).execution_options(populate_existing=True)
        )
    }
    assert statuses == {ids["toor-dal"]: "lost", ids["moong-dal"]: "packed"}


@time_machine.travel(NOW, tick=False)
async def test_a_rebuild_adds_the_kept_items_attached_after_the_task_was_built(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    owner = await user_id(db_session, PRIYA)
    await place(db_session, owner, NEXT, LOCKED, NEXT_BOX)  # locked and built first
    manager, _ = await staff(api, google, VIKRAM)
    assert await build_next(api, manager) == {"created": 1, "existing": 0}
    task = await next_task(api, manager)
    assert set(lines(task)) == {("bhindi", False), ("toor-dal", False)}

    missed, _, _ = await missed_bag(api, google, db_session)  # M4 attaches at once
    [summary] = await orders_api.summaries(db_session, [uuid.UUID(task["order_id"])])
    assert len(summary.carried) == 2 and missed != summary.order.id

    assert await build_next(api, manager) == {"created": 0, "existing": 1}
    again = await next_task(api, manager)
    assert set(lines(again)) == {
        ("bhindi", False),
        ("toor-dal", False),
        ("toor-dal", True),
        ("moong-dal", True),
    }
    assert again["version"] == task["version"] + 1
    assert await build_next(api, manager) == {"created": 0, "existing": 1}
    assert (await next_task(api, manager))["version"] == again["version"]  # nothing more to add


@time_machine.travel(NOW, tick=False)
async def test_procurement_ignores_the_carried_items(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    await missed_bag(api, google, db_session)
    owner = await user_id(db_session, PRIYA)
    next_box = await place(db_session, owner, NEXT, LOCKED, NEXT_BOX)
    [summary] = await orders_api.summaries(db_session, [next_box])
    assert len(summary.carried) == 2  # attached to the box, as the packing task will show
    manager, _ = await staff(api, google, VIKRAM)

    built = await api.post(
        f"{PLANS}/build", json={"date": NEXT.isoformat()}, headers={**manager, **key()}
    )

    assert built.status_code == 200, built.text
    plan = {x["slug"]: x for x in built.json()["lines"]}
    assert plan["toor-dal"]["committed_qty"] == 0.5  # the paid step only, not the kept 2 steps
    assert "moong-dal" not in plan  # kept on the shelf, never bought again


@time_machine.travel(NOW, tick=False)
async def test_a_box_that_carried_items_and_is_missed_twice_keeps_them_again(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    missed, ids, _ = await missed_bag(api, google, db_session)
    packer, _ = await staff(api, google, KAVITA)
    assert (await scan(api, packer, qr(missed))).status_code == 200
    owner = await user_id(db_session, PRIYA)
    next_box = await place(db_session, owner, NEXT, LOCKED, NEXT_BOX)
    manager, _ = await staff(api, google, VIKRAM)
    await build_next(api, manager)
    task = await next_task(api, manager)
    assert (await act(api, packer, task["id"], "start")).status_code == 200
    for slug, carried, qty in (
        ("bhindi", False, 1),
        ("toor-dal", False, 1),
        ("toor-dal", True, 2),
        ("moong-dal", True, 1),
    ):
        picked = await act(
            api,
            packer,
            task["id"],
            "pick",
            {"product_id": str(ids[slug]), "carried": carried, "picked_qty": qty},
        )
        assert picked.status_code == 200, picked.text
    assert (await act(api, packer, task["id"], "complete")).status_code == 200
    for to in ("dispatched", "at_society", "undelivered"):
        await orders_api.transition(db_session, next_box, to, actor="hub")
    await miss_twice(db_session, next_box)  # the kept items come back with the bag

    bag = await scan(api, packer, qr(next_box))

    assert bag.status_code == 200, bag.text
    kept = sorted(
        (k["slug"], k["qty"], k["from_order_id"] == str(next_box)) for k in bag.json()["kept"]
    )
    # The box's own toor dal (1 step), and the two kept items that rode in it: 2 steps of toor
    # dal and 1 of moong dal. The bhindi is the only thing that is not the household's.
    assert kept == [
        ("moong-dal", 1, False),
        ("toor-dal", 1, True),
        ("toor-dal", 2, False),
    ]
    assert [lot["slug"] for lot in bag.json()["lots"]] == ["bhindi"]
    items = await db_session.scalars(
        select(KeptItem).where(KeptItem.status == "kept").execution_options(populate_existing=True)
    )
    assert len(list(items)) == 3


async def pick_all(
    api: httpx2.AsyncClient,
    packer: dict[str, str],
    task: dict[str, Any],
    ids: dict[str, uuid.UUID],
    which: tuple[tuple[str, bool, int], ...],
) -> None:
    for slug, carried, qty in which:
        picked = await act(
            api,
            packer,
            task["id"],
            "pick",
            {"product_id": str(ids[slug]), "carried": carried, "picked_qty": qty},
        )
        assert picked.status_code == 200, picked.text


@time_machine.travel(NOW, tick=False)
async def test_starting_a_task_takes_the_items_attached_since_it_was_built(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    owner = await user_id(db_session, PRIYA)
    next_box = await place(db_session, owner, NEXT, LOCKED, NEXT_BOX)  # locked, built first
    manager, _ = await staff(api, google, VIKRAM)
    packer, _ = await staff(api, google, KAVITA)
    await build_next(api, manager)
    assert set(lines(await next_task(api, manager))) == {("bhindi", False), ("toor-dal", False)}
    missed, ids, _ = await missed_bag(api, google, db_session)  # attaches at once
    assert len((await orders_api.summaries(db_session, [next_box]))[0].carried) == 2
    assert missed != next_box
    task = await next_task(api, manager)

    # No rebuild: the packer's start brings the kept items onto the task before the order moves.
    started = await act(api, packer, task["id"], "start")

    assert started.status_code == 200, started.text
    assert set(lines(started.json())) == {
        ("bhindi", False),
        ("toor-dal", False),
        ("toor-dal", True),
        ("moong-dal", True),
    }
    await pick_all(
        api,
        packer,
        task,
        ids,
        (
            ("bhindi", False, 1),
            ("toor-dal", False, 1),
            ("toor-dal", True, 2),
            ("moong-dal", True, 1),
        ),
    )
    done = await act(api, packer, task["id"], "complete")
    assert done.status_code == 200, done.text
    carry_rows = await db_session.scalars(
        select(CarryOver)
        .where(CarryOver.to_order_id == next_box)
        .execution_options(populate_existing=True)
    )
    assert {r.status for r in carry_rows} == {"packed"}


@time_machine.travel(NOW, tick=False)
async def test_a_kept_item_lost_while_the_box_is_picked_leaves_it_even_when_picked(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    missed, ids, _ = await missed_bag(api, google, db_session)
    packer, _ = await staff(api, google, KAVITA)
    kept = {k["slug"]: k for k in (await scan(api, packer, qr(missed))).json()["kept"]}
    owner = await user_id(db_session, PRIYA)
    await place(db_session, owner, NEXT, LOCKED, NEXT_BOX)
    manager, _ = await staff(api, google, VIKRAM)
    await build_next(api, manager)
    task = await next_task(api, manager)
    assert (await act(api, packer, task["id"], "start")).status_code == 200
    # The packer found the toor dal, then the count shows it is not on the shelf after all.
    await pick_all(api, packer, task, ids, (("bhindi", False, 1), ("toor-dal", True, 2)))
    assert (await act(api, packer, task["id"], "complete")).json()["code"] == "hub_ops.lines_open"

    lost = await api.post(
        f"{HUB}/kept-items/{kept['toor-dal']['id']}/lost",
        json={"note": "Not on the shelf"},
        headers={**manager, **key()},
    )

    assert lost.status_code == 200, lost.text
    shown = await next_task(api, manager)
    assert set(lines(shown)) == {("bhindi", False), ("toor-dal", False), ("moong-dal", True)}
    await pick_all(api, packer, task, ids, (("toor-dal", False, 1), ("moong-dal", True, 1)))
    done = await act(api, packer, task["id"], "complete")
    assert done.status_code == 200, done.text  # the box completes without the lost item
    statuses = {
        k.product_id: k.status
        for k in await db_session.scalars(
            select(KeptItem).execution_options(populate_existing=True)
        )
    }
    assert statuses == {ids["toor-dal"]: "lost", ids["moong-dal"]: "packed"}
    assert {m.product_id for m in await moves(db_session)} == {ids["moong-dal"]}
    # Packed: nothing can be reported lost any more.
    late = await api.post(
        f"{HUB}/kept-items/{kept['moong-dal']['id']}/lost",
        json={"note": "Gone"},
        headers={**manager, **key()},
    )
    assert (late.status_code, late.json()["code"]) == (409, "hub_ops.kept_item_settled")


@time_machine.travel(NOW, tick=False)
async def test_a_second_copy_of_an_item_already_on_the_shelf_becomes_a_returned_lot(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    missed, ids, _ = await missed_bag(api, google, db_session)
    packer, _ = await staff(api, google, KAVITA)
    assert (await scan(api, packer, qr(missed))).status_code == 200
    owner = await user_id(db_session, PRIYA)
    next_box = await place(db_session, owner, NEXT, LOCKED, NEXT_BOX)
    manager, _ = await staff(api, google, VIKRAM)
    await build_next(api, manager)
    task = await next_task(api, manager)
    assert (await act(api, packer, task["id"], "start")).status_code == 200
    await pick_all(
        api,
        packer,
        task,
        ids,
        (
            ("bhindi", False, 1),
            ("toor-dal", False, 1),
            ("toor-dal", True, 2),
            ("moong-dal", True, 1),
        ),
    )
    assert (await act(api, packer, task["id"], "complete")).status_code == 200
    for to in ("dispatched", "at_society", "undelivered"):
        await orders_api.transition(db_session, next_box, to, actor="hub")
    await miss_twice(db_session, next_box)
    # The toor dal that rode in this box is still on the shelf as a kept item (as if the older
    # item had never been drawn): the bag brings a second copy of it.
    await db_session.execute(
        update(KeptItem).where(KeptItem.product_id == ids["toor-dal"]).values(status="kept")
    )
    await db_session.commit()

    bag = await scan(api, packer, qr(next_box))

    assert bag.status_code == 200, bag.text
    kept = sorted((k["slug"], k["qty"]) for k in bag.json()["kept"])
    assert kept == [("moong-dal", 1), ("toor-dal", 1)]  # the box's own toor dal; moong came back
    lots = {lot["slug"]: lot["qty_in"] for lot in bag.json()["lots"]}
    assert lots == {"bhindi": lots["bhindi"], "toor-dal": 1.0}  # 2 steps of 500 g, not shelved
