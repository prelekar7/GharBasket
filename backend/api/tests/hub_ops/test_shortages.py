"""Shortages today (M6 §3.6): the products a hub cannot cover for a date's packing lines, the
bags the gap falls on and what each family's rule allows there; a read, nothing is decided."""

import uuid
from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Any

import httpx2
import pytest
import time_machine
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.hub_ops.models import InventoryLot, PackingLine, PackingTask
from gharbasket.modules.identity.models import Household
from gharbasket.modules.orders import api as orders_api
from gharbasket.tools.seed import SEED_HUB_ID

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .conftest import KAVITA, NEHA, PRIYA, SANJAY, VIKRAM, key
from .test_packing import ARJUN, DELIVERY, MEENA, NOW, TASKS, build, grant, locked_order, staff

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("floor")]

URL = f"/v1/hubs/{SEED_HUB_ID}/shortages"
# Tomato (500 g) sells at 2_500 a step at the seed hub; its substitutes are bitter-gourd (250 g,
# 2_000), cucumber (500 g, 2_500) and brinjal (500 g, 3_000: dearer). Bhindi is 1_000 a step.
OPS = {"g": ("kg", 1000), "kg": ("kg", 1), "ml": ("l", 1000), "l": ("l", 1)}


async def stock_up(
    db: AsyncSession,
    slug: str,
    steps: int,
    *,
    unit: str | None = None,
    grade: str = "A",
    best_before: datetime | None = None,
) -> None:
    """A lot of `steps` steps of the product (in its operations unit, or `unit`), fresh for days."""
    (product_id,) = (await catalog_api.product_ids_by_slug(db, [slug])).values()
    (product,) = (await catalog_api.stock_products(db, [product_id])).values()
    ops, per = OPS.get(product.step_unit, (product.step_unit, 1))
    qty = Decimal(steps * product.step_qty) / per
    db.add(
        InventoryLot(
            hub_id=SEED_HUB_ID,
            product_id=product_id,
            unit=unit or ops,
            source="receipt",
            grade=grade,
            qty_in=qty,
            qty_on_hand=qty,
            received_at=NOW,
            best_before=best_before or NOW + timedelta(days=3),
        )
    )
    await db.commit()


async def two_boxes(db: AsyncSession) -> tuple[dict[str, uuid.UUID], uuid.UUID, uuid.UUID]:
    """Priya's box (early slot) and Meena's (later), so Meena's bag is last in the queue."""
    priya, ids = await locked_order(db, PRIYA, start=time(7))
    meena, _ = await locked_order(db, MEENA, start=time(9))
    return ids, priya, meena


async def read(api: httpx2.AsyncClient, auth: dict[str, str], **params: Any) -> httpx2.Response:
    return await api.get(URL, params={"date": DELIVERY.isoformat(), **params}, headers=auth)


def item(body: dict[str, Any], slug: str) -> dict[str, Any]:
    return next(x for x in body["items"] if x["slug"] == slug)


async def tasks_by_order(
    api: httpx2.AsyncClient, auth: dict[str, str], *orders: uuid.UUID
) -> list[dict[str, Any]]:
    listed = await api.get(TASKS, params={"date": DELIVERY.isoformat()}, headers=auth)
    by_order = {t["order_id"]: t for t in listed.json()["items"]}
    return [by_order[str(o)] for o in orders]


@time_machine.travel(NOW, tick=False)
async def test_the_gap_falls_on_the_last_bags_and_a_substitute_is_offered_where_allowed(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    manager, _ = await staff(api, google, VIKRAM)
    _, priya, meena = await two_boxes(db_session)
    await build(api, manager)
    priya_task, meena_task = await tasks_by_order(api, manager, priya, meena)
    # Tomato: 4 steps are needed and 3 are good (grade C and an expired lot do not count).
    await stock_up(db_session, "tomato", 3)
    await stock_up(db_session, "tomato", 5, grade="C")
    await stock_up(db_session, "tomato", 9, best_before=NOW - timedelta(hours=1))
    await stock_up(db_session, "bhindi", 1)  # 3 short
    await stock_up(db_session, "onion", 10)  # covered
    await stock_up(db_session, "cucumber", 4)
    await stock_up(db_session, "brinjal", 4)  # dearer than a tomato: never offered

    answer = await read(api, manager)

    assert answer.status_code == 200, answer.text
    assert answer.headers["Cache-Control"] == "no-store"
    body = answer.json()
    assert (body["hub_id"], body["date"]) == (str(SEED_HUB_ID), DELIVERY.isoformat())
    assert body["as_of"].startswith("2026-10-12T23:45")
    # Bhindi (3 short) before tomato (1 short); onion is covered and not listed.
    assert [(x["slug"], x["short_by"]) for x in body["items"]] == [("bhindi", 3), ("tomato", 1)]

    tomato = item(body, "tomato")
    assert (tomato["needed"], tomato["received"], tomato["short_by"]) == (4, 3, 1)
    assert tomato["step"] == {"qty": 500, "unit": "g", "label": "500 g"}
    assert tomato["reported"] == []
    # One short step falls on the last bag only (its two open steps cover it).
    (bag,) = tomato["bags"]
    assert (bag["task_id"], bag["bag_qr"]) == (meena_task["id"], meena_task["bag_qr"])
    assert (bag["society"], bag["flat_label"], bag["qty"]) == ("Sunrise Towers", "B wing, B-402", 2)
    assert bag["outcome"] == "substitute" and bag["refund_paise"] == 0  # cucumber costs the same
    assert bag["substitute"]["slug"] == "cucumber"
    assert bag["substitute"]["step"] == {"qty": 500, "unit": "g", "label": "500 g"}

    # Three bhindi short: the last bag's two steps and then the first bag's.
    bhindi = item(body, "bhindi")
    assert (bhindi["needed"], bhindi["received"], bhindi["short_by"]) == (4, 1, 3)
    assert [(b["task_id"], b["qty"]) for b in bhindi["bags"]] == [
        (meena_task["id"], 2),
        (priya_task["id"], 2),
    ]
    for b in bhindi["bags"]:
        assert b["outcome"] in ("substitute", "refund") and set(b) >= {"substitute", "refund_paise"}
        if b["outcome"] == "refund":
            assert (b["substitute"], b["refund_paise"]) == (None, 2 * 1_000)


@time_machine.travel(NOW, tick=False)
async def test_a_household_that_chose_a_refund_or_a_bag_part_picked_gets_a_refund(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    manager, _ = await staff(api, google, VIKRAM)
    _, _, meena = await two_boxes(db_session)
    await build(api, manager)
    meena_task = (await tasks_by_order(api, manager, meena))[0]
    await stock_up(db_session, "tomato", 1)
    await stock_up(db_session, "cucumber", 4)
    household = (await orders_api.get(db_session, meena)).household_id
    first = await read(api, manager)
    assert item(first.json(), "tomato")["bags"][0]["outcome"] == "substitute"

    # Chosen after the task was built: the read follows the preference as it stands now.
    await db_session.execute(
        update(Household).where(Household.id == household).values(shortage_preference="refund")
    )
    await db_session.commit()
    refund = (await read(api, manager)).json()

    # 3 short: Meena's bag (refund by her choice), then Priya's (a substitute is fine).
    bags = item(refund, "tomato")["bags"]
    assert [(b["task_id"], b["outcome"]) for b in bags[:1]] == [(meena_task["id"], "refund")]
    assert (bags[0]["substitute"], bags[0]["qty"], bags[0]["refund_paise"]) == (None, 2, 5_000)
    assert bags[1]["outcome"] == "substitute"

    # Part picked: a line with a step in the bag cannot be replaced, and only its open steps fall.
    await db_session.execute(
        update(PackingLine)
        .where(
            PackingLine.task_id == uuid.UUID(bags[1]["task_id"]),
            PackingLine.product_id == uuid.UUID(item(refund, "tomato")["product_id"]),
        )
        .values(picked_qty=1)
    )
    await db_session.commit()
    part = item((await read(api, manager)).json(), "tomato")["bags"][1]
    assert (part["outcome"], part["qty"], part["refund_paise"], part["substitute"]) == (
        "refund",
        1,
        2_500,
        None,
    )


@time_machine.travel(NOW, tick=False)
async def test_a_substitute_must_have_stock_for_the_line_and_a_bag_gets_it_once(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    manager, _ = await staff(api, google, VIKRAM)
    await two_boxes(db_session)
    await build(api, manager)
    # No tomato at all: both bags are short. Cucumber stock covers only one of the two lines.
    await stock_up(db_session, "cucumber", 2)
    await stock_up(db_session, "bhindi", 10)
    await stock_up(db_session, "onion", 10)

    tomato = item((await read(api, manager)).json(), "tomato")

    assert (tomato["received"], tomato["short_by"]) == (0, 4)
    assert [b["outcome"] for b in tomato["bags"]] == ["substitute", "refund"]
    assert tomato["bags"][1]["refund_paise"] == 2 * 2_500


@time_machine.travel(NOW, tick=False)
async def test_what_the_packer_recorded_shows_as_reported_even_when_stock_is_enough(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    manager, _ = await staff(api, google, VIKRAM)
    packer, _ = await staff(api, google, KAVITA)
    ids, priya, meena = await two_boxes(db_session)
    await build(api, manager)
    for slug in ("tomato", "bhindi", "onion"):
        await stock_up(db_session, slug, 10)
    await stock_up(db_session, "bitter-gourd", 10)
    gourd = (await catalog_api.product_ids_by_slug(db_session, ["bitter-gourd"]))["bitter-gourd"]
    priya_task, meena_task = await tasks_by_order(api, manager, priya, meena)
    assert (await read(api, manager)).json()["items"] == []

    # Priya's packer swaps the tomato line for bitter-gourd; Meena's packer refunds one onion.
    started = await api.post(
        f"/v1/packing-tasks/{priya_task['id']}/start",
        json={},
        headers={**packer, **key()},
    )
    assert started.status_code == 200, started.text
    swap = await api.post(
        f"/v1/packing-tasks/{priya_task['id']}/shortage",
        json={
            "product_id": str(ids["tomato"]),
            "short_qty": 2,
            "substitute_product_id": str(gourd),
        },
        headers={**packer, **key()},
    )
    assert swap.status_code == 200, swap.text
    await api.post(
        f"/v1/packing-tasks/{meena_task['id']}/start", json={}, headers={**packer, **key()}
    )
    short = await api.post(
        f"/v1/packing-tasks/{meena_task['id']}/shortage",
        json={"product_id": str(ids["onion"]), "short_qty": 1},
        headers={**packer, **key()},
    )
    assert short.status_code == 200, short.text

    body = (await read(api, manager)).json()

    assert sorted(x["slug"] for x in body["items"]) == ["onion", "tomato"]
    onion, tomato = item(body, "onion"), item(body, "tomato")
    assert (onion["needed"], onion["received"], onion["short_by"]) == (4, 10, 0)
    assert onion["bags"] == []
    (refunded,) = onion["reported"]
    assert (refunded["task_id"], refunded["outcome"], refunded["qty"]) == (
        meena_task["id"],
        "refund",
        1,
    )
    assert (refunded["refund_paise"], refunded["substitute"]) == (4_000, None)
    (swapped,) = tomato["reported"]
    assert (swapped["task_id"], swapped["outcome"], swapped["qty"]) == (
        priya_task["id"],
        "substitute",
        2,
    )
    assert swapped["substitute"]["slug"] == "bitter-gourd"
    assert swapped["substitute"]["step"]["label"] == "250 g"
    # The difference refunded: two steps at 2_500 became two at 2_000.
    assert swapped["refund_paise"] == 2 * (2_500 - 2_000)
    assert swapped["flat_label"] == "A wing, A-1203"


@time_machine.travel(NOW, tick=False)
async def test_stock_already_drawn_down_today_counts_and_stock_in_another_unit_cannot_convert(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    manager, _ = await staff(api, google, VIKRAM)
    _, priya, _ = await two_boxes(db_session)
    await build(api, manager)
    priya_task = (await tasks_by_order(api, manager, priya))[0]
    await stock_up(db_session, "tomato", 2)  # what is left after the draw-down
    await stock_up(db_session, "bhindi", 10)
    await stock_up(db_session, "onion", 10)
    short = (await read(api, manager)).json()
    assert (item(short, "tomato")["needed"], item(short, "tomato")["received"]) == (4, 2)

    # Priya's bag was packed with its 2 steps of tomato and drawn down at 12:30: they were in
    # stock when the day began, so the day has 4.
    task_id = uuid.UUID(priya_task["id"])
    await db_session.execute(
        update(PackingLine).where(PackingLine.task_id == task_id).values(picked_qty=PackingLine.qty)
    )
    await db_session.execute(
        update(PackingTask)
        .where(PackingTask.id == task_id)
        .values(status="packed", packed_at=NOW, drawn_at=NOW, bags=1, crate_no=1)
    )
    await db_session.commit()
    covered = (await read(api, manager)).json()
    assert covered["items"] == []

    # A tomato lot counted in pieces cannot be added to steps of grams: received is unknown, and
    # short_by is 0 rather than a guess.
    await stock_up(db_session, "tomato", 1, unit="piece")
    await db_session.execute(
        update(PackingTask)
        .where(PackingTask.id == task_id)
        .values(drawn_at=None, status="picking", packed_at=None)
    )
    await db_session.execute(
        update(PackingLine).where(PackingLine.task_id == task_id).values(short_qty=1, picked_qty=0)
    )
    await db_session.commit()
    unknown = item((await read(api, manager)).json(), "tomato")
    assert (unknown["received"], unknown["short_by"], unknown["needed"]) == (None, 0, 4)
    assert len(unknown["reported"]) == 1


@time_machine.travel(NOW, tick=False)
async def test_other_dates_and_empty_hubs_and_who_may_read(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    manager, _ = await staff(api, google, VIKRAM)
    packer, _ = await staff(api, google, KAVITA)
    owner, _ = await staff(api, google, SANJAY)
    admin, _ = await staff(api, google, NEHA)
    customer, _ = await staff(api, google, PRIYA)
    await locked_order(db_session, PRIYA)
    await build(api, manager)
    elsewhere = uuid.uuid4()
    await grant(db_session, ARJUN, "packer", elsewhere)
    outsider, _ = await staff(api, google, ARJUN)

    # Nothing is in stock: every product is short for the packer, the owner and the admin alike.
    for who in (packer, manager, owner, admin):
        answer = await read(api, who)
        assert answer.status_code == 200, answer.text
        assert {x["slug"] for x in answer.json()["items"]} == {"tomato", "bhindi", "onion"}
    # A date with no tasks has nothing short; the default date is today in IST.
    empty = await read(api, manager, date="2026-10-14")
    assert (empty.status_code, empty.json()["items"], empty.json()["date"]) == (
        200,
        [],
        "2026-10-14",
    )
    today = await api.get(URL, headers=manager)
    assert today.json()["date"] == "2026-10-13"

    assert (await read(api, customer)).status_code == 403
    assert (await read(api, outsider)).json()["code"] == "auth.forbidden"
    assert (await api.get(URL)).status_code == 401
    too_far = await read(api, manager, date="2026-10-16")
    assert (too_far.status_code, too_far.json()["code"]) == (422, "hub_ops.date_out_of_range")
    too_old = await read(api, manager, date="2026-10-05")
    assert (too_old.status_code, too_old.json()["code"]) == (422, "hub_ops.date_out_of_range")
