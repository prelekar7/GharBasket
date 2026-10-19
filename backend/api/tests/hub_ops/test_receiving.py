"""Goods receipt (M6 §3.4): receipts against a sent purchase order (in one trip or two) or ad hoc,
accepted and rejected quantities with grades and reasons, a lot per accepted line with its
best-before time, and a stock ledger that never moves stock twice."""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import httpx2
import pytest
import time_machine
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.hub_ops import receiving
from gharbasket.modules.hub_ops.models import (
    GoodsReceipt,
    InventoryLot,
    StockMove,
    Vendor,
)
from gharbasket.modules.identity.models import User, UserRole
from gharbasket.platform.models import AuditEntry, OutboxEvent
from gharbasket.tools.seed import SEED_HUB_ID

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .conftest import KAVITA, NEHA, PRIYA, SANJAY, VIKRAM, as_user, hub_body, key
from .test_procurement import ARJUN, DELIVERY, build, order, patch, vendor_ids, world

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("world")]
__all__ = ["world"]  # the fixture, imported so this module can use it

RECEIVING = datetime(2026, 10, 12, 22, 45, tzinfo=UTC)  # Tue 13 Oct, 04:15 IST
RECEIPTS = f"/v1/hubs/{SEED_HUB_ID}/goods-receipts"


async def slugs(db: AsyncSession, *names: str) -> dict[str, str]:
    return {s: str(i) for s, i in (await catalog_api.product_ids_by_slug(db, list(names))).items()}


async def sent_po(
    api: httpx2.AsyncClient,
    manager: dict[str, str],
    db: AsyncSession,
    lines: dict[str, int | tuple[int, int]],
    *,
    fpo: tuple[str, ...] = (),
    day: date = DELIVERY,
) -> dict[str, Any]:
    """Shinde Traders' purchase order for `lines` (steps), tomato at ₹32/kg, sent; the `fpo` slugs
    go on a draft for the Kharghar Kisan FPO (returned under "drafts")."""
    await order(db, "locked", lines, day=day)
    plan = (await build(api, manager, date=day.isoformat())).json()
    vendors = await vendor_ids(db)
    if "tomato" in lines:
        plan = (await patch(api, manager, plan, "tomato", unit_cost_paise=3200)).json()
    for slug in fpo:
        plan = (await patch(api, manager, plan, slug, vendor_id=vendors["kharghar"])).json()
    made = await api.post(
        f"/v1/procurement-plans/{plan['id']}/purchase-orders", headers={**manager, **key()}
    )
    *drafts, po = made.json()["purchase_orders"]  # by vendor name: Shinde last
    sent = await api.post(f"/v1/purchase-orders/{po['id']}/send", headers={**manager, **key()})
    assert sent.status_code == 200, sent.text
    return {**sent.json(), "drafts": drafts}


def line(product_id: str, received: float, accepted: float | None = None, **more: Any) -> dict:
    accepted = received if accepted is None else accepted
    return {
        "product_id": product_id,
        "received_qty": received,
        "accepted_qty": accepted,
        "grade": "A",
        **more,
    }


async def receive(
    api: httpx2.AsyncClient, auth: dict[str, str], *lines: dict, **body: Any
) -> httpx2.Response:
    return await api.post(
        RECEIPTS,
        json={"purchase_order_id": None, **body, "lines": list(lines)},
        headers={**auth, **key()},
    )


@time_machine.travel(RECEIVING, tick=False)
async def test_a_po_received_in_two_trips_then_refuses_more(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    manager = await as_user(api, google, VIKRAM)
    po = await sent_po(api, manager, db_session, {"tomato": 4, "onion": 2})
    ids = await slugs(db_session, "tomato", "onion", "palak")
    assert [(x["slug"], x["qty"]) for x in po["lines"]] == [("onion", 2.2), ("tomato", 2.2)]
    first = {
        "purchase_order_id": po["id"],
        "lines": [line(ids["tomato"], 2.5, 2.0, reject_reason="rotten", note="Wet  crate")],
    }
    once = {**manager, **key()}

    made = await api.post(RECEIPTS, json=first, headers=once)
    replay = await api.post(RECEIPTS, json=first, headers=once)

    assert made.status_code == 201, made.text
    assert replay.json() == made.json()
    receipt = made.json()
    assert receipt["purchase_order"] == {
        "id": po["id"],
        "number": "KHARGHAR-261013-01",
        "status": "partly_received",
    }
    assert receipt["vendor"]["name"] == "Shinde Traders"
    assert receipt["received_at"] == "2026-10-12T22:45:00Z"
    assert receipt["received_by"]["user_id"] and "name" in receipt["received_by"]
    tomato = receipt["lines"][0]
    assert {k: tomato[k] for k in ("received_qty", "accepted_qty", "rejected_qty")} == {
        "received_qty": 2.5,
        "accepted_qty": 2.0,
        "rejected_qty": 0.5,
    }
    assert (tomato["reject_reason"], tomato["unit_cost_paise"], tomato["amount_paise"]) == (
        "rotten",
        3200,  # the PO line's cost
        6400,  # for what was accepted
    )
    assert (tomato["ordered"], tomato["note"], tomato["unit"]) == (True, "Wet crate", "kg")
    assert tomato["best_before"] == "2026-10-17T22:45:00Z"  # 120 h shelf life
    assert (receipt["total_paise"], receipt["unpriced_lines"]) == (6400, 0)
    lot = await db_session.get(InventoryLot, uuid.UUID(tomato["lot_id"]))
    assert lot is not None
    assert (lot.qty_in, lot.qty_on_hand, lot.grade, lot.unit_cost_paise) == (
        Decimal(2),
        Decimal(2),
        "A",
        3200,
    )
    assert await db_session.scalar(select(func.count()).select_from(GoodsReceipt)) == 1
    po_now = (await api.get(f"/v1/purchase-orders/{po['id']}", headers=manager)).json()
    assert po_now["status"] == "partly_received"
    assert [(x["slug"], x["received_qty"], x["accepted_qty"]) for x in po_now["lines"]] == [
        ("onion", 0.0, 0.0),
        ("tomato", 2.5, 2.0),
    ]

    second = await api.post(
        RECEIPTS,
        json={
            "purchase_order_id": po["id"],
            "vendor_id": po["vendor"]["id"],
            "received_at": "2026-10-13T04:05:00+05:30",
            "lines": [
                line(ids["onion"], 2.2, grade="B", unit_cost_paise=2500, reject_reason="other"),
                line(ids["palak"], 5, 4, reject_reason="damaged", unit_cost_paise=None),
            ],
        },
        headers={**manager, **key()},
    )

    assert second.status_code == 201, second.text
    later = second.json()
    assert later["purchase_order"]["status"] == "received"
    onion, palak = (x for x in later["lines"])
    assert (onion["grade"], onion["reject_reason"], onion["amount_paise"]) == ("B", None, 5500)
    assert (palak["ordered"], palak["unit"], palak["unit_cost_paise"]) == (False, "bunch", None)
    assert (later["total_paise"], later["unpriced_lines"]) == (5500, 1)
    po_now = (await api.get(f"/v1/purchase-orders/{po['id']}", headers=manager)).json()
    assert po_now["status"] == "received" and po_now["closed_at"] is not None
    events = (
        await db_session.scalars(
            select(OutboxEvent.payload)
            .where(OutboxEvent.event_type == "hub_ops.goods_received")
            .order_by(OutboxEvent.id)
        )
    ).all()
    assert [(e["lines_count"], e["rejected_lines"], e["total_paise"]) for e in events] == [
        (1, 1, 6400),
        (2, 1, 5500),
    ]
    assert events[0]["purchase_order_id"] == po["id"]
    audited = await db_session.scalar(
        select(func.count())
        .select_from(AuditEntry)
        .where(AuditEntry.action == "goods_receipts.created")
    )
    assert audited == 2
    listed = (await api.get(RECEIPTS, headers=manager)).json()["items"]
    assert [x["id"] for x in listed] == [receipt["id"], later["id"]]  # by received_at, newest first
    assert (await api.get(RECEIPTS, params={"date": "2026-10-12"}, headers=manager)).json() == {
        "items": []
    }
    one = await api.get(f"/v1/goods-receipts/{receipt['id']}", headers=manager)
    # The purchase order's current status (the POST answered with its status after the receipt).
    assert one.json() == {**receipt, "purchase_order": later["purchase_order"]}
    await db_session.commit()

    more = await api.post(RECEIPTS, json=first, headers={**manager, **key()})
    assert (more.status_code, more.json()["code"]) == (409, "hub_ops.po_not_receivable")
    cancel = await api.post(
        f"/v1/purchase-orders/{po['id']}/cancel",
        json={"reason": "Too late"},
        headers={**manager, **key()},
    )
    assert cancel.json()["code"] == "hub_ops.po_not_cancellable"


@time_machine.travel(RECEIVING, tick=False)
async def test_ad_hoc_receipts_grades_and_lots(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    packer = await as_user(api, google, KAVITA)  # packers receive too
    ids = await slugs(db_session, "tomato", "lemon", "palak", "toor-dal")
    vendor = (await vendor_ids(db_session))["kharghar"]

    made = await receive(
        api,
        packer,
        line(ids["palak"], 10, 8, grade="C", reject_reason="rotten"),
        line(ids["lemon"], 40, grade="B", best_before="2026-10-20T00:00:00+05:30"),
        line(ids["tomato"], 3, 0, reject_reason="wrong_item"),
        line(ids["toor-dal"], 5),
        vendor_id=vendor,
    )

    assert made.status_code == 201, made.text
    receipt = made.json()
    assert receipt["purchase_order"] is None
    by_slug = {x["slug"]: x for x in receipt["lines"]}
    assert all(not x["ordered"] for x in by_slug.values())
    assert by_slug["tomato"]["lot_id"] is None  # all of it went back: no lot
    assert by_slug["palak"]["best_before"] == "2026-10-14T22:45:00Z"  # 48 h
    assert by_slug["lemon"]["best_before"] == "2026-10-19T18:30:00Z"  # as given
    assert by_slug["toor-dal"]["best_before"] == "2027-04-10T22:45:00Z"  # 4,320 h
    lots = {
        str(lot.product_id): lot
        for lot in await db_session.scalars(
            select(InventoryLot).where(InventoryLot.hub_id == SEED_HUB_ID)
        )
    }
    assert {lots[ids[s]].grade for s in ("palak", "lemon")} == {"C", "B"}
    assert ids["tomato"] not in lots
    moves = (
        await db_session.execute(select(StockMove.kind, StockMove.qty, StockMove.lot_id))
    ).all()
    assert sorted((kind, qty) for kind, qty, _ in moves) == [
        ("receive", Decimal(5)),
        ("receive", Decimal(8)),
        ("receive", Decimal(40)),
    ]
    assert all(lots[str(x.product_id)].qty_on_hand == x.qty_in for x in lots.values())


@time_machine.travel(RECEIVING, tick=False)
async def test_receipts_refuse_what_the_rules_do_not_allow(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    admin = await as_user(api, google, NEHA)
    manager = await as_user(api, google, VIKRAM)
    po = await sent_po(api, manager, db_session, {"tomato": 2, "onion": 1}, fpo=("onion",))
    assert [x["status"] for x in po["drafts"]] == ["draft"]
    other = (await api.post("/v1/admin/hubs", json=hub_body(), headers={**admin, **key()})).json()
    stranger = await api.post(
        f"/v1/hubs/{other['id']}/vendors",
        json={"name": "Vashi Mandi", "kind": "apmc_trader"},
        headers={**admin, **key()},
    )
    ids = await slugs(db_session, "tomato", "lemon", "jaggery")
    vendors = await vendor_ids(db_session)
    await db_session.execute(
        update(Vendor).where(Vendor.name == "Patil Wholesale").values(active=False)
    )
    await db_session.commit()
    tomato = line(ids["tomato"], 2)
    ad_hoc = {"vendor_id": vendors["shinde"]}

    async def refused(*lines: dict, **body: Any) -> tuple[int, str, str | None]:
        answer = await receive(api, manager, *lines, **body)
        problem = answer.json()
        return answer.status_code, problem["code"], problem.get("reason")

    invalid = "hub_ops.receipt_invalid"
    assert await refused(tomato) == (422, "request.invalid", None)  # no vendor, no PO
    assert await refused(line(ids["tomato"], 2, 3), **ad_hoc) == (
        422,
        invalid,
        "accepted_over_received",
    )
    assert await refused(line(ids["tomato"], 2, 1), **ad_hoc) == (
        422,
        invalid,
        "reject_reason_missing",
    )
    assert await refused(tomato, tomato, **ad_hoc) == (422, invalid, "duplicate_product")
    assert await refused(tomato, purchase_order_id=po["id"], vendor_id=vendors["patil"]) == (
        422,
        invalid,
        "vendor_mismatch",
    )
    for when in ("2026-10-13T04:20:00+05:30", "2026-10-12T04:00:00+05:30"):
        assert await refused(tomato, received_at=when, **ad_hoc) == (422, invalid, "received_at")
    for when in ("2026-10-13T04:00:00+05:30", "2027-10-14T04:00:00+05:30"):
        assert await refused(line(ids["tomato"], 2, best_before=when), **ad_hoc) == (
            422,
            invalid,
            "best_before",
        )
    for bad in (line(ids["jaggery"], 1), line(str(uuid.uuid4()), 1)):  # a kit; unknown
        assert (await refused(bad, **ad_hoc))[:2] == (422, "hub_ops.product_not_stocked")
    for bad in (line(ids["lemon"], 1.5), line(ids["tomato"], 0), line(ids["tomato"], 1.0001)):
        assert (await refused(bad, **ad_hoc))[:2] == (422, "hub_ops.qty_invalid")
    assert (await refused(tomato, vendor_id=vendors["patil"]))[:2] == (
        422,
        "hub_ops.vendor_inactive",
    )
    assert (await refused(tomato, vendor_id=stranger.json()["id"]))[:2] == (
        404,
        "hub_ops.vendor_not_found",
    )
    assert (await refused(tomato, purchase_order_id=str(uuid.uuid4())))[:2] == (
        404,
        "hub_ops.po_not_found",
    )
    assert await db_session.scalar(select(func.count()).select_from(GoodsReceipt)) == 0

    # A draft or cancelled purchase order is not receivable.
    await api.post(
        f"/v1/purchase-orders/{po['id']}/cancel",
        json={"reason": "Vendor sick"},
        headers={**manager, **key()},
    )
    await db_session.commit()
    for refused_po in (po, *po["drafts"]):
        assert (await refused(tomato, purchase_order_id=refused_po["id"]))[:2] == (
            409,
            "hub_ops.po_not_receivable",
        )


@time_machine.travel(RECEIVING, tick=False)
async def test_a_stock_move_from_the_same_source_is_written_once(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    manager = await as_user(api, google, VIKRAM)
    ids = await slugs(db_session, "onion")
    made = await receive(
        api, manager, line(ids["onion"], 10), vendor_id=(await vendor_ids(db_session))["shinde"]
    )
    lot = await db_session.get(InventoryLot, uuid.UUID(made.json()["lines"][0]["lot_id"]))
    assert lot is not None
    source = uuid.uuid4()

    async def waste() -> bool:
        return await receiving.move(
            db_session,
            hub_id=SEED_HUB_ID,
            product_id=lot.product_id,
            unit="kg",
            kind="waste",
            ref_id=source,
            qty=Decimal("-1.5"),
            lot=lot,
        )

    assert (await waste(), await waste()) == (True, False)
    receipt_line = made.json()["lines"][0]
    received = await db_session.scalar(select(StockMove.ref_id).where(StockMove.kind == "receive"))
    assert received is not None
    again = await receiving.move(
        db_session,
        hub_id=SEED_HUB_ID,
        product_id=lot.product_id,
        unit="kg",
        kind="receive",
        ref_id=received,
        qty=Decimal(receipt_line["accepted_qty"]),
        lot=lot,
    )
    assert again is False
    total = await db_session.scalar(
        select(func.sum(StockMove.qty)).where(StockMove.lot_id == lot.id)
    )
    assert lot.qty_on_hand == total == Decimal("8.5")


@time_machine.travel(RECEIVING, tick=False)
async def test_hub_staff_receive_only_at_their_own_hub(
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
    po = await sent_po(api, manager, db_session, {"tomato": 2})
    ids = await slugs(db_session, "tomato")
    made = (await receive(api, manager, line(ids["tomato"], 1), purchase_order_id=po["id"])).json()
    await db_session.commit()
    elsewhere = await as_user(api, google, ARJUN)

    assert (
        await receive(api, elsewhere, line(ids["tomato"], 1), vendor_id=po["vendor"]["id"])
    ).status_code == 403
    assert (await api.get(RECEIPTS, headers=elsewhere)).status_code == 403
    hidden = await api.get(f"/v1/goods-receipts/{made['id']}", headers=elsewhere)
    assert (hidden.status_code, hidden.json()["code"]) == (404, "hub_ops.receipt_not_found")
    theirs = await api.post(
        f"/v1/hubs/{other['id']}/goods-receipts",
        json={"purchase_order_id": po["id"], "lines": [line(ids["tomato"], 1)]},
        headers={**elsewhere, **key()},
    )
    assert (theirs.status_code, theirs.json()["code"]) == (404, "hub_ops.po_not_found")

    owner = await as_user(api, google, SANJAY)  # the franchise owner reads, never receives
    assert (await api.get(f"/v1/goods-receipts/{made['id']}", headers=owner)).status_code == 200
    assert (
        await receive(api, owner, line(ids["tomato"], 1), vendor_id=po["vendor"]["id"])
    ).status_code == 403
    customer = await as_user(api, google, PRIYA)
    assert (await api.get(RECEIPTS, headers=customer)).status_code == 403
    vikram = await db_session.scalar(select(User.name).where(User.phone_e164 == VIKRAM))
    assert made["received_by"]["name"] == vikram
