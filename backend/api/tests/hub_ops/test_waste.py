"""Waste logs and the waste KPI (M6 §3.5): write-offs of a lot or a product, the weight rule, the
list, and the daily summary with kg wasted per 100 kg sold."""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import httpx2
import pytest
import time_machine
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog.models import Product
from gharbasket.modules.hub_ops import api as hub_ops_api
from gharbasket.modules.hub_ops.models import (
    InventoryLot,
    PackingLine,
    PackingTask,
    StockMove,
    WasteLog,
)
from gharbasket.modules.identity.models import User, UserRole
from gharbasket.platform.models import AuditEntry, OutboxEvent
from gharbasket.tools.seed import SEED_HUB_ID

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .conftest import KAVITA, NEHA, PRIYA, SANJAY, VIKRAM, as_user, hub_body, key
from .test_procurement import ARJUN, vendor_ids, world
from .test_receiving import RECEIVING, line, receive, slugs

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("world")]
__all__ = ["world"]  # the fixture, imported so this module can use it

HUB = f"/v1/hubs/{SEED_HUB_ID}"
LOGS = f"{HUB}/waste-logs"
SUMMARY = f"{HUB}/waste-summary"
IST = "+05:30"


def at(day: str, clock: str = "10:00") -> datetime:
    return datetime.fromisoformat(f"{day}T{clock}{IST}")


async def post(
    api: httpx2.AsyncClient, auth: dict[str, str], body: dict[str, Any], **headers: str
) -> httpx2.Response:
    return await api.post(LOGS, json=body, headers={**auth, **(headers or key())})


async def stocked(
    api: httpx2.AsyncClient, manager: dict[str, str], db: AsyncSession, **qty: float
) -> dict[str, Any]:
    """One lot per product, received now: {slug: lot id}, and the products' ids under `ids`."""
    ids = await slugs(db, *qty)
    vendor = (await vendor_ids(db))["shinde"]
    made = await receive(api, manager, *(line(ids[s], q) for s, q in qty.items()), vendor_id=vendor)
    assert made.status_code == 201, made.text
    return {
        "ids": ids,
        **{x["slug"]: x["lot_id"] for x in made.json()["lines"]},
    }


async def packing_task(
    db: AsyncSession,
    day: str,
    status: str,
    lines: list[dict[str, Any]],
    hub_id: uuid.UUID = SEED_HUB_ID,
) -> None:
    task = PackingTask(
        hub_id=hub_id,
        order_id=uuid.uuid4(),
        order_kind="box",
        delivery_date=date.fromisoformat(day),
        slot_id=uuid.uuid4(),
        society_id=uuid.uuid4(),
        seq=1,
        status=status,
    )
    db.add(task)
    await db.flush()
    db.add_all(PackingLine(task_id=task.id, **x) for x in lines)
    await db.commit()


def step(product: uuid.UUID | str, qty: int, picked: int, short: int = 0, **more: Any) -> dict:
    """A line of tomato-like steps: `step_qty` 500 g unless `more` says otherwise."""
    return {
        "product_id": uuid.UUID(str(product)),
        "step_qty": 500,
        "step_unit": "g",
        "qty": qty,
        "picked_qty": picked,
        "short_qty": short,
        **more,
    }


async def log_row(db: AsyncSession, when: datetime, **more: Any) -> WasteLog:
    values: dict[str, Any] = {
        "product_id": uuid.uuid4(),
        "qty": Decimal(1),
        "unit": "kg",
        "reason": "expired",
        "cascade_to": "bin",
        **more,
    }
    row = WasteLog(hub_id=SEED_HUB_ID, at=when, **values)
    db.add(row)
    await db.commit()
    return row


async def grant(db: AsyncSession, phone: str, role: str, hub_id: uuid.UUID) -> None:
    user = await db.scalar(select(User.id).where(User.phone_e164 == phone))
    db.add(UserRole(user_id=user, role=role, scope_type="hub", scope_id=hub_id))
    await db.commit()


# --- writing off ---------------------------------------------------------------------------------


@time_machine.travel(RECEIVING, tick=False)
async def test_a_lot_write_off_moves_stock_once_and_publishes(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    manager = await as_user(api, google, VIKRAM)
    made = await stocked(api, manager, db_session, tomato=5)
    lot_id = made["tomato"]
    once = {"Idempotency-Key": uuid.uuid4().hex}
    body = {"lot_id": lot_id, "qty": 2, "reason": "spoiled", "note": "  Gone   soft "}

    first = await post(api, manager, body, **once)
    replay = await post(api, manager, body, **once)

    assert first.status_code == 201, first.text
    assert replay.status_code == 201 and replay.json() == first.json()
    log = first.json()
    vikram = await db_session.scalar(select(User.name).where(User.phone_e164 == VIKRAM))
    user_id = await db_session.scalar(select(User.id).where(User.phone_e164 == VIKRAM))
    assert log.pop("id") and log.pop("at")
    assert log == {
        "hub_id": str(SEED_HUB_ID),
        "lot_id": lot_id,
        "product_id": made["ids"]["tomato"],
        "slug": "tomato",
        "name": log["name"],
        "qty": 2.0,
        "unit": "kg",
        "kg": 2.0,
        "reason": "spoiled",
        "cascade_to": "bin",
        "offer_id": None,
        "taken_by": None,
        "note": "Gone soft",
        "logged_by": {"user_id": str(user_id), "name": vikram},
    }
    detail = (await api.get(f"/v1/lots/{lot_id}", headers=manager)).json()
    assert detail["qty_on_hand"] == 3.0
    waste = [m for m in detail["moves"] if m["kind"] == "waste"]
    assert [(m["qty"], m["reason"], m["by"]["name"]) for m in waste] == [(-2.0, "spoiled", vikram)]
    assert waste[0]["ref_id"] == first.json()["id"]
    events = (
        await db_session.scalars(
            select(OutboxEvent.payload).where(OutboxEvent.event_type == "hub_ops.waste_logged")
        )
    ).all()
    assert events == [
        {
            "waste_log_id": first.json()["id"],
            "hub_id": str(SEED_HUB_ID),
            "lot_id": lot_id,
            "product_id": made["ids"]["tomato"],
            "qty": 2.0,
            "unit": "kg",
            "kg": 2.0,
            "reason": "spoiled",
            "cascade_to": "bin",
        }
    ]
    audited = await db_session.scalar(
        select(func.count())
        .select_from(AuditEntry)
        .where(AuditEntry.action == "waste_logs.created")
    )
    assert audited == 1

    # The rest of the lot can go; one more thousandth cannot.
    rest = await post(api, manager, {"lot_id": lot_id, "qty": 3, "reason": "pest"})
    assert rest.status_code == 201, rest.text
    await db_session.commit()
    over = await post(api, manager, {"lot_id": lot_id, "qty": 0.001, "reason": "other"})
    assert (over.status_code, over.json()["code"]) == (409, "hub_ops.qty_exceeds_stock")
    assert over.json()["qty_on_hand"] == 0
    lot = await db_session.get(InventoryLot, uuid.UUID(lot_id), populate_existing=True)
    assert lot is not None and lot.qty_on_hand == 0
    moved = await db_session.scalar(
        select(func.sum(StockMove.qty)).where(StockMove.lot_id == uuid.UUID(lot_id))
    )
    assert moved == 0


@time_machine.travel(RECEIVING, tick=False)
async def test_a_product_write_off_is_unlotted_and_kg_follows_the_unit(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    manager = await as_user(api, google, VIKRAM)
    ids = await slugs(db_session, "onion", "milk", "palak", "cauliflower", "cabbage")
    await db_session.execute(
        update(Product)
        .where(Product.slug == "cabbage")
        .values(nutrition=text("nutrition - 'unit_g'"))
    )
    await db_session.commit()

    kgs = {}
    for slug, qty in (
        ("onion", 1.5),
        ("milk", 2),
        ("palak", 3),
        ("cauliflower", 2),
        ("cabbage", 1),
    ):
        made = await post(api, manager, {"product_id": ids[slug], "qty": qty, "reason": "damaged"})
        assert made.status_code == 201, made.text
        assert made.json()["lot_id"] is None
        kgs[slug] = (made.json()["unit"], made.json()["kg"])
        await db_session.commit()

    assert kgs == {
        "onion": ("kg", 1.5),  # kg: itself
        "milk": ("l", 2.0),  # a litre counted as a kilo
        "palak": ("bunch", 0.3),  # 3 bunches of 100 g
        "cauliflower": ("piece", 1.2),  # 2 pieces of 600 g
        "cabbage": ("piece", None),  # no unit_g: unknown
    }
    moves = (
        await db_session.execute(
            select(StockMove.lot_id, StockMove.kind, StockMove.qty, StockMove.reason).where(
                StockMove.kind == "waste"
            )
        )
    ).all()
    assert len(moves) == 5
    assert {(m.lot_id, m.kind, m.reason) for m in moves} == {(None, "waste", "damaged")}
    assert sorted(m.qty for m in moves) == [-3, -2, -2, -1.5, -1]
    stored = await db_session.scalar(
        select(WasteLog.kg).where(WasteLog.product_id == uuid.UUID(ids["cabbage"]))
    )
    assert stored is None


@time_machine.travel(RECEIVING, tick=False)
async def test_write_offs_refuse_what_cannot_be_written_off(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    admin = await as_user(api, google, NEHA)
    manager = await as_user(api, google, VIKRAM)
    made = await stocked(api, manager, db_session, palak=4, tomato=2)
    ids = made["ids"]
    other = (await api.post("/v1/admin/hubs", json=hub_body(), headers={**admin, **key()})).json()
    jaggery = (await slugs(db_session, "jaggery"))["jaggery"]  # a kit in this world: not stocked
    await db_session.commit()

    def code(response: httpx2.Response) -> tuple[int, str]:
        return response.status_code, response.json()["code"]

    lot = made["tomato"]
    assert code(await post(api, manager, {"qty": 1, "reason": "spoiled"})) == (
        422,
        "request.invalid",
    )
    both = {"lot_id": lot, "product_id": ids["tomato"], "qty": 1, "reason": "spoiled"}
    assert code(await post(api, manager, both)) == (422, "request.invalid")
    for bad in (
        {"lot_id": lot, "qty": 0, "reason": "spoiled"},
        {"lot_id": lot, "qty": -1, "reason": "spoiled"},
        {"lot_id": lot, "qty": 1, "reason": "surplus"},  # the cascade's reasons are not for staff
        {"lot_id": lot, "qty": 1, "reason": "returned"},
        {"lot_id": lot, "qty": 1, "reason": "spoiled", "cascade_to": "ngo"},
        {"lot_id": lot, "qty": 1, "reason": "spoiled", "note": "x" * 301},
    ):
        assert code(await post(api, manager, bad)) == (422, "request.invalid"), bad
    assert code(await post(api, manager, {"lot_id": lot, "qty": 0.0004, "reason": "other"})) == (
        422,
        "hub_ops.qty_invalid",
    )
    assert code(
        await post(api, manager, {"lot_id": made["palak"], "qty": 1.5, "reason": "other"})
    ) == (
        422,
        "hub_ops.qty_invalid",
    )  # whole bunches
    assert code(await post(api, manager, {"lot_id": lot, "qty": 2.5, "reason": "other"})) == (
        409,
        "hub_ops.qty_exceeds_stock",
    )
    assert code(
        await post(api, manager, {"lot_id": str(uuid.uuid4()), "qty": 1, "reason": "other"})
    ) == (
        404,
        "hub_ops.lot_not_found",
    )
    unstocked = await post(api, manager, {"product_id": jaggery, "qty": 1, "reason": "other"})
    assert code(unstocked) == (422, "hub_ops.product_not_stocked")
    assert unstocked.json()["product_ids"] == [jaggery]
    nobody = str(uuid.uuid4())
    assert code(await post(api, manager, {"product_id": nobody, "qty": 1, "reason": "other"})) == (
        422,
        "hub_ops.product_not_stocked",
    )
    # Another hub's lot does not exist for a manager of this hub, and this hub's not for theirs.
    elsewhere_lot = made["palak"]
    await db_session.execute(
        update(InventoryLot)
        .where(InventoryLot.id == uuid.UUID(elsewhere_lot))
        .values(hub_id=uuid.UUID(other["id"]))
    )
    await db_session.commit()
    assert code(
        await post(api, manager, {"lot_id": elsewhere_lot, "qty": 1, "reason": "other"})
    ) == (
        404,
        "hub_ops.lot_not_found",
    )
    # Idempotency: a key is required, and a changed body under the same key is refused.
    missing = await api.post(
        LOGS, json={"lot_id": lot, "qty": 1, "reason": "other"}, headers=manager
    )
    assert code(missing) == (400, "request.idempotency_key_required")
    reused = {"Idempotency-Key": uuid.uuid4().hex}
    first = await post(
        api, manager, {"product_id": ids["tomato"], "qty": 1, "reason": "other"}, **reused
    )
    assert first.status_code == 201, first.text
    await db_session.commit()
    changed = await post(
        api, manager, {"product_id": ids["tomato"], "qty": 2, "reason": "other"}, **reused
    )
    assert code(changed) == (422, "request.idempotency_mismatch")
    assert await db_session.scalar(select(func.count()).select_from(WasteLog)) == 1


@time_machine.travel(RECEIVING, tick=False)
async def test_who_may_write_off_and_who_may_read(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    admin = await as_user(api, google, NEHA)
    manager = await as_user(api, google, VIKRAM)
    other = (await api.post("/v1/admin/hubs", json=hub_body(), headers={**admin, **key()})).json()
    await grant(db_session, ARJUN, "hub_manager", uuid.UUID(other["id"]))
    made = await stocked(api, manager, db_session, tomato=9)
    await db_session.commit()
    body = {"lot_id": made["tomato"], "qty": 1, "reason": "spoiled"}

    packer = await as_user(api, google, KAVITA)  # a packer of the hub writes off and reads
    assert (await post(api, packer, body)).status_code == 201
    await db_session.commit()
    assert (await api.get(LOGS, headers=packer)).status_code == 200
    assert (await api.get(SUMMARY, headers=packer)).status_code == 200

    owner = await as_user(api, google, SANJAY)  # the franchise owner reads, never writes off
    assert (await api.get(LOGS, headers=owner)).status_code == 200
    assert (await api.get(SUMMARY, headers=owner)).status_code == 200
    assert (await post(api, owner, body)).status_code == 403
    assert (await api.get(LOGS, headers=admin)).status_code == 200  # the admin reads
    assert (await post(api, admin, body)).status_code == 403  # waste:write is hub staff's

    elsewhere = await as_user(api, google, ARJUN)  # a manager of another hub sees none of it
    assert (await api.get(LOGS, headers=elsewhere)).status_code == 403
    assert (await api.get(SUMMARY, headers=elsewhere)).status_code == 403
    assert (await post(api, elsewhere, body)).status_code == 403
    theirs = await api.get(f"/v1/hubs/{other['id']}/waste-logs", headers=elsewhere)
    assert theirs.json() == {"items": [], "next_cursor": None}
    unknown = await api.post(
        f"/v1/hubs/{uuid.uuid4()}/waste-logs", json=body, headers={**admin, **key()}
    )
    assert unknown.status_code == 403
    customer = await as_user(api, google, PRIYA)
    assert (await api.get(LOGS, headers=customer)).status_code == 403
    assert (await api.get(SUMMARY, headers=customer)).status_code == 403
    assert (await post(api, customer, body)).status_code == 403
    assert (await api.get(LOGS)).status_code == 401


# --- reading -------------------------------------------------------------------------------------


@time_machine.travel(RECEIVING, tick=False)
async def test_logs_list_newest_first_with_filters_and_a_cursor(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    manager = await as_user(api, google, VIKRAM)
    made = await stocked(api, manager, db_session, tomato=9)
    ids = made["ids"]
    written = []
    for body in (
        {"lot_id": made["tomato"], "qty": 1, "reason": "spoiled"},
        {"product_id": ids["tomato"], "qty": 2, "reason": "pest"},
    ):
        response = await post(api, manager, body)
        assert response.status_code == 201, response.text
        written.append(response.json()["id"])
        await db_session.commit()
    partner = uuid.uuid4()
    kitchen = await log_row(
        db_session,
        at("2026-10-11", "09:00"),
        product_id=uuid.UUID(ids["tomato"]),
        reason="surplus",
        cascade_to="kitchen",
        taken_by_kind="kitchen",
        taken_by_partner_id=partner,
        taken_by_name="Shree Tiffins",
    )
    ngo = await log_row(
        db_session,
        at("2026-10-10", "09:00"),
        product_id=uuid.UUID(ids["tomato"]),
        reason="returned",
        cascade_to="ngo",
        taken_by_kind="ngo",
        taken_by_name="Annadaan",
    )
    # IST days: the 11th ends at 23:59, the 12th starts at 00:00.
    for when, row_id in (
        (at("2026-10-11", "23:59"), written[0]),
        (at("2026-10-12", "00:00"), written[1]),
    ):
        await db_session.execute(
            update(WasteLog).where(WasteLog.id == uuid.UUID(row_id)).values(at=when)
        )
    await db_session.commit()

    everything = (await api.get(LOGS, headers=manager)).json()
    assert [x["id"] for x in everything["items"]] == [
        written[1],
        written[0],
        str(kitchen.id),
        str(ngo.id),
    ]
    assert everything["next_cursor"] is None
    by_id = {x["id"]: x for x in everything["items"]}
    assert by_id[str(kitchen.id)]["taken_by"] == {
        "kind": "kitchen",
        "partner_id": str(partner),
        "name": "Shree Tiffins",
    }
    assert by_id[str(ngo.id)]["taken_by"] == {"kind": "ngo", "name": "Annadaan"}
    assert by_id[str(ngo.id)]["logged_by"] is None  # the 12:30 job
    assert (by_id[str(ngo.id)]["reason"], by_id[str(ngo.id)]["cascade_to"]) == ("returned", "ngo")

    first = (await api.get(LOGS, params={"limit": 3}, headers=manager)).json()
    assert [x["id"] for x in first["items"]] == [written[1], written[0], str(kitchen.id)]
    rest = (
        await api.get(LOGS, params={"limit": 3, "cursor": first["next_cursor"]}, headers=manager)
    ).json()
    assert [x["id"] for x in rest["items"]] == [str(ngo.id)] and rest["next_cursor"] is None

    def ids_of(page: dict[str, Any]) -> list[str]:
        return [x["id"] for x in page["items"]]

    eleventh = {"from": "2026-10-11", "to": "2026-10-11"}
    assert ids_of((await api.get(LOGS, params=eleventh, headers=manager)).json()) == [
        written[0],
        str(kitchen.id),
    ]
    since = (await api.get(LOGS, params={"from": "2026-10-12"}, headers=manager)).json()
    assert ids_of(since) == [written[1]]
    until = (await api.get(LOGS, params={"to": "2026-10-10"}, headers=manager)).json()
    assert ids_of(until) == [str(ngo.id)]
    bins = await api.get(LOGS, params={"cascade_to": "bin"}, headers=manager)
    assert ids_of(bins.json()) == [written[1], written[0]]
    ngos = await api.get(LOGS, params={"cascade_to": "ngo"}, headers=manager)
    assert ids_of(ngos.json()) == [str(ngo.id)]

    for params, code in (
        ({"from": "2026-10-12", "to": "2026-10-11"}, "hub_ops.date_out_of_range"),
        ({"cursor": "not-a-cursor"}, "request.invalid_cursor"),
        ({"cursor": "e30"}, "request.invalid_cursor"),  # {}: no position
        ({"limit": 201}, "request.invalid"),
        ({"cascade_to": "compost"}, "request.invalid"),
    ):
        refused = await api.get(LOGS, params=params, headers=manager)
        assert (refused.status_code, refused.json()["code"]) == (422, code), params


# --- the KPI -------------------------------------------------------------------------------------


async def kpi_world(
    api: httpx2.AsyncClient, admin: dict[str, str], manager: dict[str, str], db: AsyncSession
) -> None:
    """The 12th (yesterday) with packed, queued and short lines, and waste around it."""
    ids = await slugs(db, "tomato", "onion", "palak", "cabbage", "bhindi", "cauliflower")
    await db.execute(
        update(Product)
        .where(Product.slug == "cabbage")
        .values(nutrition=text("nutrition - 'unit_g'"))
    )
    await db.commit()
    p = ids
    await packing_task(  # packed: 20 + 10 + 0.3 kg sold; 2 bunches short
        db,
        "2026-10-12",
        "packed",
        [
            step(p["tomato"], 40, 40),
            step(p["onion"], 10, 10, step_qty=1, step_unit="kg"),
            step(p["palak"], 5, 3, 2, step_qty=1, step_unit="bunch"),
        ],
    )
    await packing_task(  # packed: a substitute weighed as itself, a piece with no weight
        db,
        "2026-10-12",
        "packed",
        [
            step(
                p["bhindi"],
                2,
                2,
                step_qty=1,
                step_unit="piece",
                substitute_product_id=uuid.UUID(p["cauliflower"]),
            ),
            step(p["cabbage"], 2, 2, step_qty=1, step_unit="piece"),
            step(p["tomato"], 2, 2, carried=True),  # a kept item: sold on an earlier day
        ],
    )
    await packing_task(db, "2026-10-12", "queued", [step(p["tomato"], 10, 0)])  # ordered only
    await packing_task(db, "2026-10-13", "packed", [step(p["tomato"], 100, 100)])  # another day
    other = await api.post("/v1/admin/hubs", json=hub_body(), headers={**admin, **key()})
    await packing_task(
        db,
        "2026-10-12",
        "packed",
        [step(p["tomato"], 100, 100)],
        hub_id=uuid.UUID(other.json()["id"]),
    )
    lot = (await stocked(api, manager, db, tomato=5))["tomato"]
    written = await post(api, manager, {"lot_id": lot, "qty": 0.5, "reason": "spoiled"})
    assert written.status_code == 201, written.text
    await db.commit()
    await db.execute(
        update(WasteLog)
        .where(WasteLog.id == uuid.UUID(written.json()["id"]))
        .values(at=at("2026-10-12", "10:00"))
    )
    await db.commit()
    tomato = uuid.UUID(p["tomato"])
    bin_, kit, ngo = dict(product_id=tomato), dict(cascade_to="kitchen"), dict(cascade_to="ngo")
    await log_row(db, at("2026-10-12", "00:00"), kg=Decimal("0.8"), **bin_)
    await log_row(db, at("2026-10-12", "23:59"), kg=None, **bin_)  # unweighed
    await log_row(db, at("2026-10-12", "12:30"), kg=Decimal("8"), reason="surplus", **kit)
    await log_row(db, at("2026-10-12", "12:30"), kg=Decimal("3.5"), reason="returned", **ngo)
    await log_row(db, at("2026-10-11", "23:59"), kg=Decimal(100), **bin_)  # the day before
    await log_row(db, at("2026-10-13", "00:00"), kg=Decimal(999), **bin_)  # the day after


@time_machine.travel(RECEIVING, tick=False)
async def test_the_summary_counts_bin_as_waste_and_rescues_the_rest(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    manager = await as_user(api, google, VIKRAM)
    await kpi_world(api, await as_user(api, google, NEHA), manager, db_session)

    default = await api.get(SUMMARY, headers=manager)  # the last 7 days up to yesterday (the 12th)
    assert default.status_code == 200, default.text
    body = default.json()
    assert set(body) == {"hub_id", "from", "to", "days", "totals"}
    assert (body["hub_id"], body["from"], body["to"]) == (
        str(SEED_HUB_ID),
        "2026-10-06",
        "2026-10-12",
    )
    assert [d["date"] for d in body["days"]] == [f"2026-10-{n:02d}" for n in range(6, 13)]
    days = {d["date"]: d for d in body["days"]}
    assert set(days["2026-10-12"]) == {
        "date",
        "sold_kg",
        "wasted_kg",
        "rescued_kg",
        "waste_per_100kg",
        "stockout_pct",
        "unweighed_lines",
    }
    # 20 kg tomato + 10 kg onion + 3 bunches of palak (0.3 kg) + 2 cauliflowers in place of bhindi
    # (1.2 kg); the cabbage has no weight and the kept tomato is not sold today.
    assert days["2026-10-12"] == {
        "date": "2026-10-12",
        "sold_kg": 31.5,
        "wasted_kg": 1.3,  # 0.5 written off + 0.8; the unweighed log is left out
        "rescued_kg": {"kitchen": 8.0, "ngo": 3.5},
        "waste_per_100kg": 4.13,  # 1.3 / 31.5 * 100
        "stockout_pct": 5.8,  # 2 palak + 2 substituted of 69 steps ordered
        "unweighed_lines": 2,  # the cabbage line and the log without a weight
    }
    assert days["2026-10-11"] == {
        "date": "2026-10-11",
        "sold_kg": 0.0,
        "wasted_kg": 100.0,
        "rescued_kg": {"kitchen": 0.0, "ngo": 0.0},
        "waste_per_100kg": None,  # nothing sold
        "stockout_pct": None,  # nothing ordered
        "unweighed_lines": 0,
    }
    assert days["2026-10-06"]["waste_per_100kg"] is None
    assert body["totals"] == {
        "sold_kg": 31.5,
        "wasted_kg": 101.3,
        "rescued_kg": {"kitchen": 8.0, "ngo": 3.5},
        "waste_per_100kg": 321.59,
        "stockout_pct": 5.8,
        "unweighed_lines": 2,
    }

    one = await api.get(SUMMARY, params={"from": "2026-10-12", "to": "2026-10-12"}, headers=manager)
    assert [d["date"] for d in one.json()["days"]] == ["2026-10-12"]
    assert one.json()["totals"]["waste_per_100kg"] == 4.13
    thirteenth = await api.get(
        SUMMARY, params={"from": "2026-10-13", "to": "2026-10-13"}, headers=manager
    )
    day = thirteenth.json()["days"][0]  # 100 steps sold, a 999 kg bin log
    assert (day["sold_kg"], day["wasted_kg"], day["waste_per_100kg"]) == (50.0, 999.0, 1998.0)
    until = await api.get(SUMMARY, params={"to": "2026-10-08"}, headers=manager)
    assert (until.json()["from"], until.json()["to"]) == ("2026-10-02", "2026-10-08")

    facade = await hub_ops_api.waste_summary(
        db_session, SEED_HUB_ID, date(2026, 10, 11), date(2026, 10, 12)
    )
    assert [x.date for x in facade] == [date(2026, 10, 11), date(2026, 10, 12)]
    assert facade[1] == hub_ops_api.WasteDay(
        date=date(2026, 10, 12),
        sold_kg=31.5,
        wasted_kg=1.3,
        rescued_kg={"kitchen": 8.0, "ngo": 3.5},
        waste_per_100kg=4.13,
        stockout_pct=5.8,
        unweighed_lines=2,
    )
    assert facade[0].waste_per_100kg is None and facade[0].wasted_kg == 100.0


@time_machine.travel(RECEIVING, tick=False)
async def test_a_substitute_is_sold_by_its_own_step(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    """Packing keeps the ordered product's step on the line; the packer picked the substitute's
    steps, so 2 steps of bitter gourd (250 g) in place of tomato (500 g) are 0.5 kg, not 1 kg."""
    api = clients()
    manager = await as_user(api, google, VIKRAM)
    p = await slugs(db_session, "tomato", "bitter-gourd")
    await packing_task(
        db_session,
        "2026-10-12",
        "packed",
        [step(p["tomato"], 2, 2, substitute_product_id=uuid.UUID(p["bitter-gourd"]))],
    )

    one = await api.get(SUMMARY, params={"from": "2026-10-12", "to": "2026-10-12"}, headers=manager)

    day = one.json()["days"][0]
    assert (day["sold_kg"], day["stockout_pct"], day["unweighed_lines"]) == (0.5, 100.0, 0)


@time_machine.travel(RECEIVING, tick=False)
async def test_the_summary_covers_at_most_31_days(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    manager = await as_user(api, google, VIKRAM)

    month = await api.get(
        SUMMARY, params={"from": "2026-09-12", "to": "2026-10-12"}, headers=manager
    )
    assert month.status_code == 200 and len(month.json()["days"]) == 31
    assert month.json()["totals"]["waste_per_100kg"] is None
    for params in (
        {"from": "2026-09-11", "to": "2026-10-12"},  # 32 days
        {"from": "2026-10-12", "to": "2026-10-11"},  # backwards
        {"from": "2026-08-01"},  # up to yesterday: far over
        {"from": "2026-10-13"},  # after yesterday
    ):
        refused = await api.get(SUMMARY, params=params, headers=manager)
        assert (refused.status_code, refused.json()["code"]) == (422, "hub_ops.date_out_of_range")
    assert (
        await api.get(SUMMARY, params={"from": "yesterday"}, headers=manager)
    ).status_code == 422
