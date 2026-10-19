"""Stock (M6 §3.4): the inventory view, lots with their moves, physical counts, the usable stock
and last costs that procurement plans read, and the weekly vendor scores."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
import time_machine
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.hub_ops.models import InventoryLot, PurchaseOrder, StockMove, VendorScore
from gharbasket.modules.identity.models import User, UserRole
from gharbasket.platform.jobs import run_job
from gharbasket.platform.models import AuditEntry
from gharbasket.tools.seed import SEED_HUB_ID

from ..conftest import make_settings
from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .conftest import KAVITA, NEHA, PRIYA, SANJAY, VIKRAM, as_user, hub_body, key
from .test_procurement import ARJUN, build, lines_by_slug, order, vendor_ids, world
from .test_receiving import RECEIVING, line, receive, sent_po, slugs

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("world")]
__all__ = ["world"]  # the fixture, imported so this module can use it

INVENTORY = f"/v1/hubs/{SEED_HUB_ID}/inventory"
SETTINGS = make_settings()


async def moves_total(db: AsyncSession, lot_id: str) -> Decimal | None:
    return await db.scalar(
        select(func.sum(StockMove.qty)).where(StockMove.lot_id == uuid.UUID(lot_id))
    )


@time_machine.travel(RECEIVING, tick=False)
async def test_the_inventory_lists_lots_by_expiry_and_counts_correct_them(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    manager = await as_user(api, google, VIKRAM)
    ids = await slugs(db_session, "tomato", "palak")
    vendors = await vendor_ids(db_session)
    first = await receive(
        api,
        manager,
        line(ids["tomato"], 5, unit_cost_paise=3000),
        line(ids["palak"], 10),
        vendor_id=vendors["shinde"],
    )
    soon = "2026-10-14T12:00:00+05:30"
    second = await receive(
        api,
        manager,
        line(ids["tomato"], 2, grade="C", best_before=soon),
        vendor_id=vendors["kharghar"],
    )
    fresh_id = first.json()["lines"][1]["lot_id"]
    assert first.json()["lines"][1]["slug"] == "tomato"

    stock = (await api.get(INVENTORY, headers=manager)).json()

    items = {x["slug"]: x for x in stock["items"]}
    assert stock["as_of"] == "2026-10-12T22:45:00Z"
    tomato = items["tomato"]
    assert (tomato["unit"], tomato["shelf_life_h"], tomato["on_hand_qty"]) == ("kg", 120, 7.0)
    assert tomato["packed_not_drawn_qty"] == 0
    assert [(x["grade"], x["qty_on_hand"], x["vendor"]["name"]) for x in tomato["lots"]] == [
        ("C", 2.0, "Kharghar Kisan FPO"),  # first expiry first
        ("A", 5.0, "Shinde Traders"),
    ]
    fresh = tomato["lots"][1]
    assert (fresh["id"], fresh["receipt_id"], fresh["source"], fresh["unit_cost_paise"]) == (
        fresh_id,
        first.json()["id"],
        "receipt",
        3000,
    )
    assert tomato["lots"][0]["best_before"] == "2026-10-14T06:30:00Z"
    assert items["palak"]["on_hand_qty"] == 10
    only = await api.get(INVENTORY, params={"product_id": ids["palak"]}, headers=manager)
    assert [x["slug"] for x in only.json()["items"]] == ["palak"]
    expiring = await api.get(
        INVENTORY, params={"expiring_before": "2026-10-15T00:00:00+05:30"}, headers=manager
    )
    assert [lot["id"] for x in expiring.json()["items"] for lot in x["lots"]] == [
        second.json()["lines"][0]["lot_id"]
    ]

    once = {**manager, **key()}
    body = {"qty_on_hand": 4.5, "reason": "Two  squashed"}
    counted = await api.post(f"/v1/lots/{fresh_id}/count", json=body, headers=once)
    replay = await api.post(f"/v1/lots/{fresh_id}/count", json=body, headers=once)
    same = await api.post(f"/v1/lots/{fresh_id}/count", json=body, headers={**manager, **key()})

    assert counted.status_code == 200, counted.text
    assert replay.json() == counted.json() == same.json()
    assert (counted.json()["qty_in"], counted.json()["qty_on_hand"]) == (5.0, 4.5)
    lot = (await api.get(f"/v1/lots/{fresh_id}", headers=manager)).json()
    vikram = await db_session.scalar(select(User.name).where(User.phone_e164 == VIKRAM))
    assert [(m["kind"], m["qty"], m["reason"], m["by"]["name"]) for m in lot["moves"]] == [
        ("receive", 5.0, None, vikram),
        ("count", -0.5, "Two squashed", vikram),  # a count that matches moves nothing
    ]
    assert await moves_total(db_session, fresh_id) == Decimal("4.5")
    audited = (
        await db_session.scalars(
            select(AuditEntry.after).where(AuditEntry.action == "inventory.counted")
        )
    ).all()
    assert audited == [{"qty_on_hand": 4.5, "reason": "Two squashed"}] * 2
    emptied = await api.post(
        f"/v1/lots/{fresh_id}/count",
        json={"qty_on_hand": 0, "reason": "All sold off"},
        headers={**manager, **key()},
    )
    assert emptied.json()["qty_on_hand"] == 0
    left = (await api.get(INVENTORY, params={"product_id": ids["tomato"]}, headers=manager)).json()
    assert [x["grade"] for x in left["items"][0]["lots"]] == ["C"]
    assert await moves_total(db_session, fresh_id) == 0
    await db_session.commit()

    palak_lot = first.json()["lines"][0]["lot_id"]
    for lot_id, bad in (
        (fresh_id, {"qty_on_hand": -1, "reason": "Spilt"}),
        (palak_lot, {"qty_on_hand": 2.5, "reason": "Half a bunch"}),
    ):
        refused = await api.post(f"/v1/lots/{lot_id}/count", json=bad, headers={**manager, **key()})
        assert (refused.status_code, refused.json()["code"]) == (422, "hub_ops.qty_invalid")
    short = await api.post(
        f"/v1/lots/{fresh_id}/count",
        json={"qty_on_hand": 1, "reason": "ok"},
        headers={**manager, **key()},
    )
    assert short.json()["code"] == "request.invalid"
    missing = await api.get(f"/v1/lots/{uuid.uuid4()}", headers=manager)
    assert (missing.status_code, missing.json()["code"]) == (404, "hub_ops.lot_not_found")


@time_machine.travel(RECEIVING, tick=False)
async def test_only_the_hubs_managers_count_and_staff_read_their_own_hub(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    admin = await as_user(api, google, NEHA)
    manager = await as_user(api, google, VIKRAM)
    other = (await api.post("/v1/admin/hubs", json=hub_body(), headers={**admin, **key()})).json()
    rider = await db_session.scalar(select(User.id).where(User.phone_e164 == ARJUN))
    db_session.add(
        UserRole(
            user_id=rider, role="hub_manager", scope_type="hub", scope_id=uuid.UUID(other["id"])
        )
    )
    ids = await slugs(db_session, "onion")
    made = await receive(
        api, manager, line(ids["onion"], 3), vendor_id=(await vendor_ids(db_session))["shinde"]
    )
    lot_id = made.json()["lines"][0]["lot_id"]
    await db_session.commit()
    count = {"qty_on_hand": 2, "reason": "Recount"}

    for phone in (KAVITA, SANJAY):  # a packer and the franchise owner read, never count
        staff = await as_user(api, google, phone)
        assert (await api.get(INVENTORY, headers=staff)).status_code == 200
        assert (await api.get(f"/v1/lots/{lot_id}", headers=staff)).status_code == 200
        refused = await api.post(f"/v1/lots/{lot_id}/count", json=count, headers={**staff, **key()})
        assert refused.status_code == 403
    elsewhere = await as_user(api, google, ARJUN)
    assert (await api.get(INVENTORY, headers=elsewhere)).status_code == 403
    for method, path in (("GET", f"/v1/lots/{lot_id}"), ("POST", f"/v1/lots/{lot_id}/count")):
        hidden = await api.request(method, path, json=count, headers={**elsewhere, **key()})
        assert (hidden.status_code, hidden.json()["code"]) == (404, "hub_ops.lot_not_found")
    theirs = await api.get(f"/v1/hubs/{other['id']}/inventory", headers=elsewhere)
    assert theirs.json()["items"] == []
    customer = await as_user(api, google, PRIYA)
    assert (await api.get(INVENTORY, headers=customer)).status_code == 403
    assert (await api.get(f"/v1/lots/{lot_id}", headers=admin)).status_code == 200


@time_machine.travel(RECEIVING, tick=False)
async def test_plans_net_usable_stock_and_take_the_last_receipt_cost(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    manager = await as_user(api, google, VIKRAM)
    ids = await slugs(db_session, "tomato", "onion")
    shinde = (await vendor_ids(db_session))["shinde"]
    tomato, onion = ids["tomato"], ids["onion"]
    yesterday = "2026-10-12T20:00:00+05:30"
    for lines, when in (
        ([line(onion, 1, unit_cost_paise=2400)], yesterday),  # older: not the last cost
        (
            [
                line(tomato, 5, unit_cost_paise=3000),
                line(onion, 2, grade="B", unit_cost_paise=2500),
            ],
            None,
        ),
        ([line(tomato, 2, grade="C", unit_cost_paise=1500)], None),  # never netted
        (
            [line(tomato, 1, 0, reject_reason="wrong_item", unit_cost_paise=9999)],
            None,
        ),  # none taken
        ([line(tomato, 3, best_before="2026-10-14T20:00:00+05:30")], None),  # gone by the 14th
    ):
        made = await receive(api, manager, *lines, vendor_id=shinde, received_at=when)
        assert made.status_code == 201, made.text
    await order(
        db_session, "locked", {"tomato": 20, "onion": 3, "carrot": 2}, day=date(2026, 10, 14)
    )

    plan = (await build(api, manager, date="2026-10-14")).json()

    found = {
        slug: (x["committed_qty"], x["on_hand_qty"], x["suggested_qty"], x["unit_cost_paise"])
        for slug, x in lines_by_slug(plan).items()
    }
    assert found == {
        "tomato": (10, 5, 6, 1500),  # the C lot's cost is the latest that took goods
        "onion": (3, 3, 0.3, 2500),
        "carrot": (1, 0, 1.1, None),
    }
    await order(db_session, "locked", {"tomato": 20})
    today = lines_by_slug((await build(api, manager)).json())["tomato"]
    assert (today["on_hand_qty"], today["suggested_qty"]) == (8, 3)  # the 3 kg lasts the 13th
    more = await receive(api, manager, line(onion, 5, unit_cost_paise=2600), vendor_id=shinde)
    assert more.status_code == 201
    rebuilt = lines_by_slug((await build(api, manager, date="2026-10-14")).json())["onion"]
    assert (rebuilt["on_hand_qty"], rebuilt["suggested_qty"], rebuilt["unit_cost_paise"]) == (
        8,
        0,
        2500,  # rebuilds keep a line's cost
    )
    netted = await db_session.scalar(
        select(func.sum(InventoryLot.qty_on_hand)).where(
            InventoryLot.product_id == uuid.UUID(tomato)
        )
    )
    assert netted == Decimal(10)  # 5 A + 2 C + 3 short-dated on the shelf; only 5 usable


async def test_vendor_scores_weigh_the_components_a_vendor_has(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    with time_machine.travel(datetime(2026, 10, 7, 16, 0, tzinfo=UTC), tick=False):  # Wed 21:30
        manager = await as_user(api, google, VIKRAM)
        po = await sent_po(
            api,
            manager,
            db_session,
            {"tomato": 4, "onion": 2, "palak": 10},
            fpo=("palak",),
            day=date(2026, 10, 8),
        )
        fpo = po["drafts"][0]
        sent = await api.post(f"/v1/purchase-orders/{fpo['id']}/send", headers={**manager, **key()})
        assert sent.status_code == 200
    ids = await slugs(db_session, "tomato", "onion")
    vendors = await vendor_ids(db_session)
    with time_machine.travel(datetime(2026, 10, 7, 23, 0, tzinfo=UTC), tick=False):  # Thu 04:30
        manager = await as_user(api, google, VIKRAM)
        shinde = await receive(
            api,
            manager,
            line(ids["tomato"], 2.2, 2.0, reject_reason="rotten"),  # ₹32/kg from the PO
            line(ids["onion"], 1.1, grade="B", unit_cost_paise=2000),  # half the order
            purchase_order_id=po["id"],
        )
        patil = await receive(
            api, manager, line(ids["tomato"], 1, unit_cost_paise=2800), vendor_id=vendors["patil"]
        )
        assert (shinde.status_code, patil.status_code) == (201, 201)
    # The 12:30 run (§3.5) cancels what never came.
    await db_session.execute(
        update(PurchaseOrder)
        .where(PurchaseOrder.id == uuid.UUID(fpo["id"]))
        .values(status="cancelled", cancel_reason="not_delivered")
    )
    monday = datetime(2026, 10, 11, 22, 3, tzinfo=UTC)  # Mon 12 Oct 03:33 IST

    first = await run_job(
        db_session, "compute_vendor_scores", settings=SETTINGS, scheduled_for=monday
    )
    again = await run_job(
        db_session, "compute_vendor_scores", settings=SETTINGS, scheduled_for=monday
    )

    assert first.summary == again.summary == {"week": "2026-10-05", "vendors": 3}
    rows = {
        row.vendor_id: row
        for row in await db_session.scalars(
            select(VendorScore).execution_options(populate_existing=True)
        )
    }
    by_name = {name: rows[uuid.UUID(vendor_id)] for name, vendor_id in vendors.items()}

    def parts(row: VendorScore) -> tuple:
        return (row.freshness, row.price, row.reliability, row.defects, row.score)

    d = Decimal
    # Shinde: one of two accepted lines is grade A; tomato at ₹32 against a median of ₹30, onion
    # alone; fill (1 + 0.5) / 2 and on time; 0.2 of 2.2 kg tomato rejected.
    assert parts(by_name["shinde"]) == (d("0.5"), d("0.969"), d("0.875"), d("0.045"), d("0.76"))
    assert (by_name["shinde"].pos_count, by_name["shinde"].receipt_lines_count) == (1, 2)
    # Patil: no purchase order, so reliability is left out and the rest re-weighted.
    assert parts(by_name["patil"]) == (d(1), d(1), None, d(0), d(1))
    # Kharghar Kisan FPO: sent and never delivered.
    assert parts(by_name["kharghar"]) == (None, None, d(0), None, d(0))
    assert all(row.week == date(2026, 10, 5) for row in rows.values())
    manager = await as_user(api, google, VIKRAM)
    listed = (await api.get(f"/v1/hubs/{SEED_HUB_ID}/vendors", headers=manager)).json()["items"]
    assert {x["name"]: x["score"] for x in listed} == {
        "Kharghar Kisan FPO": 0.0,
        "Patil Wholesale": 1.0,
        "Shinde Traders": 0.76,
    }
