"""The packer's moves (M6 §3.2, ADR-0026 §6): next, start, pick, shortage, complete and release,
driving the order through M4 with actor `packer` (or `hub`), offline replays and crates."""

import uuid
from datetime import UTC, datetime
from typing import Any

import httpx2
import pytest
import time_machine
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.hub_ops import api as hub_ops_api
from gharbasket.modules.hub_ops.models import PackingEvent
from gharbasket.modules.identity.models import Household
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.orders import service as order_service
from gharbasket.modules.orders.models import OrderEvent
from gharbasket.tools.seed import SEED_HUB_ID, SEED_SOCIETY_ID

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from ..orders.test_issues import events
from ..orders.test_shortages import refunds
from .conftest import KAVITA, PRIYA, SANJAY, VIKRAM, key
from .test_packing import (
    ARJUN,
    MEENA,
    NOW,
    TASKS,
    build,
    grant,
    locked_order,
    queue,
    staff,
    user_id,
)

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("floor")]


async def act(
    api: httpx2.AsyncClient,
    auth: dict[str, str],
    task_id: str,
    move: str,
    body: dict[str, Any] | None = None,
    idem: str | None = None,
) -> httpx2.Response:
    return await api.post(
        f"/v1/packing-tasks/{task_id}/{move}",
        json=body,
        headers={**auth, "Idempotency-Key": idem or uuid.uuid4().hex},
    )


async def timeline(
    db: AsyncSession, order_id: uuid.UUID
) -> list[tuple[str, str, uuid.UUID | None]]:
    rows = await db.execute(
        select(OrderEvent.to_status, OrderEvent.actor, OrderEvent.actor_user_id)
        .where(OrderEvent.order_id == order_id)
        .order_by(OrderEvent.id)
    )
    return [tuple(row) for row in rows.all()]  # pyright: ignore[reportReturnType]


def line(task: dict[str, Any], product_id: uuid.UUID) -> dict[str, Any]:
    return next(x for x in task["lines"] if x["product_id"] == str(product_id))


@time_machine.travel(NOW, tick=False)
async def test_start_pick_and_complete_move_the_order_with_actor_packer(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    manager, _ = await staff(api, google, VIKRAM)
    order_id, ids = await locked_order(db_session, PRIYA)
    await build(api, manager)
    packer, kavita = await staff(api, google, KAVITA)

    claimed = await api.post(f"{TASKS}/next", headers={**packer, **key()})

    assert claimed.status_code == 200, claimed.text
    task = claimed.json()
    assert (task["status"], task["packer"]) == (
        "picking",
        {"user_id": str(kavita), "name": "Kavita More"},
    )
    assert task["started_at"] is not None
    # Lines by catalog category (vegetables, then roots and tubers), then name; steps as ordered.
    assert [x["slug"] for x in task["lines"]] == ["bhindi", "tomato", "onion"]
    assert line(task, ids["tomato"])["step"] == {"qty": 500, "unit": "g", "label": "500 g"}
    assert (await orders_api.get(db_session, order_id)).status == "picking"
    again = await api.post(f"{TASKS}/next", headers={**packer, **key()})
    assert again.json()["id"] == task["id"]  # the packer's own task in picking comes back

    open_lines = await act(api, packer, task["id"], "complete")
    assert (open_lines.status_code, open_lines.json()["code"]) == (409, "hub_ops.lines_open")
    assert len(open_lines.json()["product_ids"]) == 3
    for slug in ("tomato", "bhindi", "onion"):
        picked = await act(
            api, packer, task["id"], "pick", {"product_id": str(ids[slug]), "picked_qty": 2}
        )
        assert picked.status_code == 200, picked.text
    version = picked.json()["version"]
    # Absolute: the same pick under a new key (an offline retry) changes nothing.
    same = await act(
        api, packer, task["id"], "pick", {"product_id": str(ids["onion"]), "picked_qty": 2}
    )
    assert same.json()["version"] == version
    for bad in (3, -1):
        refused = await act(
            api, packer, task["id"], "pick", {"product_id": str(ids["onion"]), "picked_qty": bad}
        )
        assert (refused.status_code, refused.json()["code"]) == (422, "hub_ops.qty_invalid")
    unknown = await act(
        api, packer, task["id"], "pick", {"product_id": str(uuid.uuid4()), "picked_qty": 1}
    )
    assert unknown.json()["code"] == "hub_ops.line_not_found"

    done = await act(api, packer, task["id"], "complete", {"client_at": "2026-10-12T23:50:00Z"})

    assert done.status_code == 200, done.text
    packed = done.json()
    assert (packed["status"], packed["crate_no"], packed["bags"], packed["lines_done"]) == (
        "packed",
        1,
        1,
        3,
    )
    assert await timeline(db_session, order_id) == [
        ("approved", "customer", None),
        ("locked", "system", None),
        ("picking", "packer", kavita),
        ("packed", "packer", kavita),
    ]
    published = await events(db_session, "orders.packed")
    assert [(e["order_id"], e["actor"], e["kind"]) for e in published] == [
        (str(order_id), "packer", "box")
    ]
    stored = (
        await db_session.execute(
            select(PackingEvent.action, PackingEvent.client_at).order_by(PackingEvent.id)
        )
    ).all()
    assert [a for a, _ in stored] == ["start", "pick", "pick", "pick", "complete"]
    assert stored[-1][1] == datetime(2026, 10, 12, 23, 50, tzinfo=UTC)
    after = await act(
        api, packer, task["id"], "pick", {"product_id": str(ids["onion"]), "picked_qty": 1}
    )
    assert (after.status_code, after.json()["code"]) == (409, "hub_ops.task_packed")
    bag = await hub_ops_api.bag_for_order(db_session, order_id)
    assert bag == hub_ops_api.BagRef(f"GB1:{order_id.hex}", 1, 1, "packed")
    nothing = await api.post(f"{TASKS}/next", headers={**packer, **key()})
    assert nothing.status_code == 204


@time_machine.travel(NOW, tick=False)
async def test_shortages_refund_once_or_substitute_the_whole_line(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    manager, _ = await staff(api, google, VIKRAM)
    order_id, ids = await locked_order(db_session, PRIYA)
    owner = await user_id(db_session, PRIYA)
    await build(api, manager)
    packer, _ = await staff(api, google, KAVITA)
    task = (await api.post(f"{TASKS}/next", headers={**packer, **key()})).json()
    found = await catalog_api.product_ids_by_slug(db_session, ["bitter-gourd", "brinjal", "potato"])
    gourd, brinjal, potato = found["bitter-gourd"], found["brinjal"], found["potato"]
    before = await billing_api.balance(db_session, owner)
    await act(api, packer, task["id"], "pick", {"product_id": str(ids["onion"]), "picked_qty": 1})

    # Onion: one step picked, one short and refunded; the offline replay moves no more money.
    short = {"product_id": str(ids["onion"]), "short_qty": 1}
    tap = uuid.uuid4().hex
    first = await act(api, packer, task["id"], "shortage", short, idem=tap)
    replay = await act(api, packer, task["id"], "shortage", short, idem=tap)
    assert first.status_code == 200, first.text
    assert replay.json() == first.json() and replay.headers["Idempotent-Replayed"] == "true"
    onion = line(first.json(), ids["onion"])
    assert (onion["picked_qty"], onion["short_qty"], onion["done"]) == (1, 1, True)
    assert await billing_api.balance(db_session, owner) == before + 4_000
    assert await refunds(db_session, order_id) == [f"refund:{order_id}:short_{ids['onion'].hex}"]
    await db_session.commit()
    twice = await act(api, packer, task["id"], "shortage", short)
    assert (twice.status_code, twice.json()["code"]) == (409, "hub_ops.shortage_recorded")
    changed = await act(api, packer, task["id"], "shortage", {**short, "short_qty": 2}, idem=tap)
    assert (changed.status_code, changed.json()["code"]) == (422, "request.idempotency_mismatch")

    # Tomato: only a whole line, only a listed substitute, never dearer.
    for qty, substitute, reason in (
        (1, gourd, "partial"),
        (2, potato, "not_a_substitute"),
        (2, brinjal, "pricier"),
    ):
        refused = await act(
            api,
            packer,
            task["id"],
            "shortage",
            {
                "product_id": str(ids["tomato"]),
                "short_qty": qty,
                "substitute_product_id": str(substitute),
            },
        )
        assert (refused.status_code, refused.json()["code"]) == (422, "hub_ops.substitute_invalid")
        assert refused.json()["reason"] == reason
    for qty in (0, 3):
        refused = await act(
            api,
            packer,
            task["id"],
            "shortage",
            {"product_id": str(ids["bhindi"]), "short_qty": qty},
        )
        assert refused.json()["code"] == "hub_ops.qty_invalid"
    swapped = await act(
        api,
        packer,
        task["id"],
        "shortage",
        {"product_id": str(ids["tomato"]), "short_qty": 2, "substitute_product_id": str(gourd)},
    )
    assert swapped.status_code == 200, swapped.text
    tomato = line(swapped.json(), ids["tomato"])
    assert tomato["substitute"]["slug"] == "bitter-gourd"
    assert tomato["substitute"]["step"]["label"] == "250 g"
    assert (tomato["picked_qty"], tomato["short_qty"], tomato["done"]) == (0, 0, False)
    assert await billing_api.balance(db_session, owner) == before + 4_000 + 1_000
    # The packer picks the substitute's steps, by either product id.
    picked = await act(api, packer, task["id"], "pick", {"product_id": str(gourd), "picked_qty": 2})
    assert line(picked.json(), ids["tomato"])["done"] is True
    order_lines = {ln.product_id: ln for ln in await order_service.lines_of(db_session, order_id)}
    assert order_lines[gourd].substituted_from == ids["tomato"]
    assert order_lines[ids["onion"]].shortage_qty == 1
    assert len(await events(db_session, "orders.shortage_substituted")) == 2


@time_machine.travel(NOW, tick=False)
async def test_a_household_that_chose_a_refund_is_never_substituted_at_packing(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    manager, _ = await staff(api, google, VIKRAM)
    order_id, ids = await locked_order(db_session, PRIYA)
    owner = await user_id(db_session, PRIYA)
    await build(api, manager)
    packer, _ = await staff(api, google, KAVITA)
    task = (await api.post(f"{TASKS}/next", headers={**packer, **key()})).json()
    gourd = (await catalog_api.product_ids_by_slug(db_session, ["bitter-gourd"]))["bitter-gourd"]
    before = await billing_api.balance(db_session, owner)
    # Chosen after the task was built: packing reads it when the shortage is recorded (M1 §3.4).
    household = (await orders_api.get(db_session, order_id)).household_id
    await db_session.execute(
        update(Household).where(Household.id == household).values(shortage_preference="refund")
    )
    await db_session.commit()
    whole = {"product_id": str(ids["tomato"]), "short_qty": 2}

    refused = await act(
        api, packer, task["id"], "shortage", {**whole, "substitute_product_id": str(gourd)}
    )

    assert (refused.status_code, refused.json()["code"]) == (422, "hub_ops.substitute_invalid")
    assert refused.json()["reason"] == "refund_preferred"
    assert await events(db_session, "orders.shortage_substituted") == []
    assert await billing_api.balance(db_session, owner) == before
    # The packer records the shortage as a refund instead.
    refunded = await act(api, packer, task["id"], "shortage", whole)
    assert refunded.status_code == 200, refunded.text
    tomato = line(refunded.json(), ids["tomato"])
    assert (tomato["short_qty"], tomato["substitute"], tomato["done"]) == (2, None, True)
    assert await refunds(db_session, order_id) == [f"refund:{order_id}:short_{ids['tomato'].hex}"]


@time_machine.travel(NOW, tick=False)
async def test_one_packer_per_task_and_a_hub_manager_releases_it(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    manager, vikram = await staff(api, google, VIKRAM)
    first_order, ids = await locked_order(db_session, PRIYA)
    second_order, _ = await locked_order(db_session, MEENA)
    await build(api, manager)
    await grant(db_session, ARJUN, "packer", SEED_HUB_ID)
    kavita_auth, kavita = await staff(api, google, KAVITA)
    arjun_auth, arjun = await staff(api, google, ARJUN)

    mine = (await api.post(f"{TASKS}/next", headers={**kavita_auth, **key()})).json()
    theirs = (await api.post(f"{TASKS}/next", headers={**arjun_auth, **key()})).json()

    assert (mine["order_id"], theirs["order_id"]) == (str(first_order), str(second_order))
    await db_session.commit()
    taken = await act(api, arjun_auth, mine["id"], "start")
    assert (taken.status_code, taken.json()["code"]) == (409, "hub_ops.task_taken")
    pick = {"product_id": str(ids["onion"]), "picked_qty": 1}
    not_yours = await act(api, arjun_auth, mine["id"], "pick", pick)
    assert (not_yours.status_code, not_yours.json()["code"]) == (403, "hub_ops.not_your_task")
    by_packer = await act(api, kavita_auth, mine["id"], "release")
    assert (by_packer.status_code, by_packer.json()["code"]) == (403, "auth.forbidden")
    mismatch = await act(
        api, kavita_auth, mine["id"], "start", {"bag_qr": f"GB1:{second_order.hex}"}
    )
    assert (mismatch.status_code, mismatch.json()["code"]) == (422, "hub_ops.bag_mismatch")

    released = await act(api, manager, mine["id"], "release")

    assert released.status_code == 200, released.text
    assert (released.json()["status"], released.json()["packer"]) == ("picking", None)
    restarted = await act(
        api, arjun_auth, mine["id"], "start", {"bag_qr": f"GB1:{first_order.hex}"}
    )
    assert restarted.json()["packer"]["user_id"] == str(arjun)
    # The order moved once, by Kavita; the hand-over does not move it again.
    assert await timeline(db_session, first_order) == [
        ("approved", "customer", None),
        ("locked", "system", None),
        ("picking", "packer", kavita),
    ]
    # A hub manager may pick and complete any task; the order's actor is then `hub`.
    for slug in ("tomato", "bhindi", "onion"):
        await act(api, manager, mine["id"], "pick", {"product_id": str(ids[slug]), "picked_qty": 2})
    packed = await act(api, manager, mine["id"], "complete", {"bags": 2})
    assert (packed.json()["bags"], packed.json()["crate_no"]) == (2, 1)
    assert (await timeline(db_session, first_order))[-1] == ("packed", "hub", vikram)
    queued = await queue(api, manager, status="packed")
    assert [t["id"] for t in queued] == [mine["id"]]


@time_machine.travel(NOW, tick=False)
async def test_crates_take_three_bags_and_an_order_moved_elsewhere_leaves_the_task_alone(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    manager, _ = await staff(api, google, VIKRAM)
    box = {"onion": (1, 4_000)}
    orders = [(await locked_order(db_session, phone, box))[0] for phone in (PRIYA, MEENA, PRIYA)]
    console, _ = await locked_order(db_session, MEENA, box)
    await build(api, manager)
    await orders_api.transition(db_session, console, "packed", actor="hub")  # in the console
    await db_session.commit()
    packer, _ = await staff(api, google, KAVITA)
    onion = (await catalog_api.product_ids_by_slug(db_session, ["onion"]))["onion"]
    console_task = next(t for t in await queue(api, manager) if t["order_id"] == str(console))

    refused = await act(api, packer, console_task["id"], "start")

    assert (refused.status_code, refused.json()["code"]) == (409, "orders.illegal_transition")
    crates = []
    for bags in (2, 1, 1):
        task = (await api.post(f"{TASKS}/next", headers={**packer, **key()})).json()
        await act(api, packer, task["id"], "pick", {"product_id": str(onion), "picked_qty": 1})
        done = (await act(api, packer, task["id"], "complete", {"bags": bags})).json()
        crates.append((done["order_id"], done["crate_no"]))
    # Queue: Priya (A-1203) twice, then Meena (B-402); bags 2 + 1 fill crate 1, the next starts 2.
    assert crates == [(str(orders[0]), 1), (str(orders[2]), 1), (str(orders[1]), 2)]
    # `next` passed over the task whose order the console packed; it stays queued.
    assert (await api.post(f"{TASKS}/next", headers={**packer, **key()})).status_code == 204
    left = await api.get(f"/v1/packing-tasks/{console_task['id']}", headers=packer)
    assert (left.json()["status"], left.json()["version"]) == ("queued", 1)
    summary = (await api.get(f"/v1/hubs/{SEED_HUB_ID}/packing-summary", headers=manager)).json()
    assert summary["societies"] == [
        {
            "society_id": str(SEED_SOCIETY_ID),
            "name": "Sunrise Towers",
            "bags": 5,
            "packed_bags": 4,
            "crates": 2,
        }
    ]


@time_machine.travel(NOW, tick=False)
async def test_only_the_hubs_packers_and_managers_pack(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    manager, _ = await staff(api, google, VIKRAM)
    await locked_order(db_session, PRIYA)
    await build(api, manager)
    task = (await queue(api, manager))[0]
    await grant(db_session, ARJUN, "packer", uuid.uuid4())  # a packer of another hub
    outsider, _ = await staff(api, google, ARJUN)
    owner, _ = await staff(api, google, SANJAY)
    await db_session.commit()

    started = await act(api, outsider, task["id"], "start")
    assert (started.status_code, started.json()["code"]) == (404, "hub_ops.task_not_found")
    claimed = await api.post(f"{TASKS}/next", headers={**outsider, **key()})
    assert claimed.status_code == 403
    # The franchise owner reads the queue but does not pack.
    assert (await act(api, owner, task["id"], "start")).status_code == 403
    queued = await act(
        api, manager, task["id"], "pick", {"product_id": str(uuid.uuid4()), "picked_qty": 1}
    )
    assert (queued.status_code, queued.json()["code"]) == (409, "hub_ops.task_not_started")
    no_key = await api.post(f"/v1/packing-tasks/{task['id']}/start", headers=manager)
    assert no_key.json()["code"] == "request.idempotency_key_required"
