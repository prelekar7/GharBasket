"""Procurement plans and purchase orders (M6 §3.3): the plan from the cut-off's orders plus a
buffer (or M10's forecast), rebuilt at 21:10, 23:05 and on a new forecast without losing the hub
manager's edits; audited line edits; one draft purchase order per vendor, sent by hand."""

import uuid
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

import httpx2
import pytest
import time_machine
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.catalog.models import Product
from gharbasket.modules.hub_ops import jobs, procurement
from gharbasket.modules.hub_ops.models import ProcurementPlan, Vendor
from gharbasket.modules.identity.models import User, UserRole
from gharbasket.modules.orders import service as order_service
from gharbasket.modules.orders.models import Order, OrderLine
from gharbasket.platform.jobs import run_job
from gharbasket.platform.models import AuditEntry, OutboxEvent
from gharbasket.tools.seed import SEED_HUB_ID, seed, seed_catalog, seed_hubs

from ..conftest import make_settings
from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .conftest import KAVITA, NEHA, PRIYA, SANJAY, VIKRAM, as_user, hub_body, key

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("world")]

NOW = datetime(2026, 10, 12, 16, 0, tzinfo=UTC)  # Mon 12 Oct, 21:30 IST
DELIVERY = date(2026, 10, 13)
AT_2110 = datetime(2026, 10, 12, 15, 40, tzinfo=UTC)  # 21:10 IST, for 13 Oct
AT_2305 = datetime(2026, 10, 12, 17, 35, tzinfo=UTC)  # 23:05 IST, for 13 Oct
ARJUN = "+919800001006"  # a rider, made hub manager of another hub in the scoping test
PLANS = f"/v1/hubs/{SEED_HUB_ID}/procurement-plans"
SETTINGS = make_settings()
# A box locked at the cut-off: steps of each product (and steps short at packing).
BOX = {"tomato": 3, "palak": 10, "lemon": 2, "toor-dal": 1, "bhindi": (4, 1)}


@pytest.fixture
async def world(db_session: AsyncSession) -> None:
    """The seed people, catalog (with jaggery made a kit) and hub with its three vendors."""
    await seed(db_session)
    await seed_catalog(db_session)
    await seed_hubs(db_session)
    await db_session.execute(update(Product).where(Product.slug == "jaggery").values(kind="kit"))
    await db_session.commit()


async def order(
    db: AsyncSession,
    status: str,
    lines: dict[str, int | tuple[int, int]],
    *,
    kind: str = "box",
    day: date = DELIVERY,
    hub_id: uuid.UUID = SEED_HUB_ID,
    substitutes: dict[str, str] | None = None,
) -> uuid.UUID:
    """An order in `status` with lines {slug: steps or (steps, short)}; `substitutes` maps a
    slug on the order to the product it replaced at packing. Committed."""
    slot = await order_service.create_slot(
        db,
        hub_id=hub_id,
        label=f"Slot {uuid.uuid4().hex[:8]}",
        kind="box",
        start=time(7),
        end=time(9),
        capacity=10,
    )
    row = Order(
        household_id=uuid.uuid4(),
        hub_id=hub_id,
        society_id=uuid.uuid4(),
        kind=kind,
        delivery_date=day,
        slot_id=slot.id,
        cutoff_at=NOW,
        status=status,
        subtotal_paise=0,
        total_paise=0,
    )
    db.add(row)
    await db.flush()
    slugs = [*lines, *(substitutes or {}).values()]
    ids = await catalog_api.product_ids_by_slug(db, slugs)
    products = await catalog_api.stock_products(db, list(ids.values()))
    for slug, value in lines.items():
        qty, short = value if isinstance(value, tuple) else (value, 0)
        product = products[ids[slug]]
        replaced = (substitutes or {}).get(slug)
        db.add(
            OrderLine(
                order_id=row.id,
                product_id=product.id,
                kind="addon" if kind == "addon" else "box",
                qty=qty,
                step_qty=product.step_qty,
                step_unit=product.step_unit,
                unit_price_paise=0,
                shortage_qty=short,
                substituted_from=None if replaced is None else ids[replaced],
            )
        )
    await db.commit()
    return row.id


async def build(api: httpx2.AsyncClient, auth: dict[str, str], **body: Any) -> httpx2.Response:
    return await api.post(
        f"{PLANS}/build", json={"date": DELIVERY.isoformat(), **body}, headers={**auth, **key()}
    )


def lines_by_slug(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {line["slug"]: line for line in plan["lines"]}


async def vendor_ids(db: AsyncSession) -> dict[str, str]:
    rows = await db.execute(select(Vendor.name, Vendor.id).where(Vendor.hub_id == SEED_HUB_ID))
    return {name.split()[0].lower(): str(vendor_id) for name, vendor_id in rows.all()}


async def patch(
    api: httpx2.AsyncClient, auth: dict[str, str], plan: dict[str, Any], slug: str, **body: Any
) -> httpx2.Response:
    product = lines_by_slug(plan)[slug]["product_id"]
    return await api.patch(
        f"/v1/procurement-plans/{plan['id']}/lines/{product}",
        json=body,
        headers={**auth, "If-Match": f'"{plan["version"]}"'},
    )


# --- building ------------------------------------------------------------------------------------


@time_machine.travel(NOW, tick=False)
async def test_the_plan_counts_paid_box_and_addon_lines_and_shows_at_risk_ones(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await order(db_session, "locked", {**BOX, "jaggery": 2})  # jaggery is a kit: never bought
    await order(db_session, "picking", {"tomato": 1}, kind="addon")
    await order(
        db_session, "packed", {"onion": 1, "cucumber": 2}, substitutes={"cucumber": "tomato"}
    )
    await order(db_session, "payment_failed", {"potato": 2})
    await order(db_session, "locked", {"carrot": 5}, kind="tiffin")
    for status in ("approved", "cancelled"):
        await order(db_session, status, {"garlic": 1})
    await order(db_session, "locked", {"beetroot": 1}, day=date(2026, 10, 14))
    api = clients()
    manager = await as_user(api, google, VIKRAM)

    built = await build(api, manager)

    assert built.status_code == 200, built.text
    plan = built.json()
    assert built.headers["ETag"] == '"1"'
    assert (plan["status"], plan["source"], plan["model"], plan["buffer_pct"]) == (
        "draft",
        "fallback",
        None,
        10,
    )
    found = {
        slug: (x["unit"], x["committed_qty"], x["at_risk_qty"], x["buffer_qty"], x["order_qty"])
        for slug, x in lines_by_slug(plan).items()
    }
    assert found == {
        "palak": ("bunch", 10, 0, 0.5, 11),  # 48 h: 5 %, rounded up to a whole bunch
        "cucumber": ("kg", 1, 0, 0.1, 1.1),  # the substitute counts, not the tomato
        "lemon": ("piece", 8, 0, 0.8, 9),  # four to a step
        "bhindi": ("kg", 0.75, 0, 0.075, 0.9),  # one of four 250 g steps short
        "tomato": ("kg", 2, 0, 0.2, 2.2),  # box and add-on
        "onion": ("kg", 1, 0, 0.1, 1.1),
        "potato": ("kg", 0, 2, 0, 0),  # in the payment grace: shown, not bought
        "toor-dal": ("kg", 0.5, 0, 0.05, 0.6),
    }
    assert [x["slug"] for x in plan["lines"]] == list(found)  # by shelf, then name
    palak = lines_by_slug(plan)["palak"]
    assert (palak["shelf_life_h"], palak["basis"], palak["edited"]) == (48, "buffer", False)
    assert palak["vendor"]["name"] == "Shinde Traders"  # the default: no purchase history
    assert (palak["unit_cost_paise"], palak["amount_paise"], palak["purchase_order"]) == (
        None,
        None,
        None,
    )
    assert plan["totals"] == {"lines": 8, "order_paise": 0, "unpriced_lines": 8}
    ready = (
        await db_session.execute(
            select(OutboxEvent.payload).where(
                OutboxEvent.event_type == "hub_ops.procurement_plan_ready"
            )
        )
    ).scalars()
    assert [(e["plan_id"], e["source"], e["lines_count"], e["version"]) for e in ready] == [
        (plan["id"], "fallback", 8, 1)
    ]
    assert (await api.get(PLANS, params={"date": "2026-10-13"}, headers=manager)).json() == plan
    for day in ("2026-10-12", "2026-10-15"):  # today ... today + 2
        late = await build(api, manager, date=day)
        assert late.status_code == (200 if day == "2026-10-12" else 422)
    missing = await api.get(PLANS, params={"date": "2026-10-14"}, headers=manager)
    assert (missing.status_code, missing.json()["code"]) == (404, "hub_ops.plan_not_found")
    far = await api.get(PLANS, params={"date": "2026-10-01"}, headers=manager)
    assert far.json()["code"] == "hub_ops.date_out_of_range"


async def test_a_forecast_changes_the_basis_of_the_lines_it_covers(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = await catalog_api.product_ids_by_slug(db_session, ["tomato", "milk", "apple", "jaggery"])
    await order(db_session, "locked", {"tomato": 4, "milk": 10, "palak": 6})

    async def forecast(*_: Any) -> dict[str, Any]:
        def item(slug: str, q50: float, qtau: float) -> dict[str, Any]:
            return {"product_id": str(ids[slug]), "uncommitted_q50": q50, "uncommitted_qtau": qtau}

        # Steps: tomato 500 g, milk 500 ml (48 h), apple 500 g; jaggery is a kit here.
        items = [item("tomato", 1, 2.5), item("milk", 2, 4), item("apple", 1, 2)]
        return {"model": "AutoETS", "items": [*items, item("jaggery", 1, 1)]}

    monkeypatch.setattr(procurement, "_forecast_for", forecast)

    plan, outcome = await procurement.build(db_session, SEED_HUB_ID, DELIVERY)

    assert plan is not None and outcome == "built"
    assert (plan.source, plan.model) == ("ml", "AutoETS")
    lines = {line.product_id: line for line in await procurement.lines_of(db_session, plan.id)}
    tomato, milk, apple = lines[ids["tomato"]], lines[ids["milk"]], lines[ids["apple"]]
    assert (tomato.basis, tomato.forecast_q50, tomato.buffer_qty, tomato.suggested_qty) == (
        "forecast",
        Decimal("0.5"),
        Decimal("1.25"),
        Decimal("3.3"),
    )
    # Milk in litres, capped at 5 % of 5 l for its 48 h shelf life.
    assert (milk.unit, milk.forecast_qtau, milk.buffer_qty, milk.suggested_qty) == (
        "l",
        Decimal(2),
        Decimal("0.25"),
        Decimal("5.3"),
    )
    assert (apple.committed_qty, apple.basis, apple.order_qty) == (0, "forecast", Decimal(1))
    palak = next(x for x in lines.values() if x.unit == "bunch")  # not in the forecast
    assert (palak.basis, palak.forecast_q50, palak.buffer_qty) == ("buffer", None, Decimal("0.3"))
    assert ids["jaggery"] not in lines


# --- rebuilding and editing ----------------------------------------------------------------------


@time_machine.travel(NOW, tick=False)
async def test_rebuilds_keep_edits_and_follow_the_orders_elsewhere(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await order(db_session, "locked", {"tomato": 2, "onion": 1})
    unpaid = await order(db_session, "payment_failed", {"tomato": 4})
    api = clients()
    manager = await as_user(api, google, VIKRAM)
    plan = (await build(api, manager)).json()
    vendors = await vendor_ids(db_session)
    edit = {
        "order_qty": 5.0,
        "reason": "Vendor sells 5 kg crates",
        "vendor_id": vendors["kharghar"],
    }
    plan = (await patch(api, manager, plan, "tomato", **edit, unit_cost_paise=3205)).json()
    assert plan["version"] == 2
    await order(db_session, "locked", {"carrot": 2})
    await db_session.execute(update(Order).where(Order.id == unpaid).values(status="locked"))
    await db_session.commit()

    first = await run_job(
        db_session, "build_procurement_plan", settings=SETTINGS, scheduled_for=AT_2110
    )
    again = await run_job(
        db_session, "refresh_procurement_plan", settings=SETTINGS, scheduled_for=AT_2305
    )

    assert (first.status, first.summary) == (
        "ok",
        {"delivery_date": "2026-10-13", "hubs": 1, "rebuilt": 1},
    )
    assert again.summary == {"delivery_date": "2026-10-13", "hubs": 1, "unchanged": 1}
    rebuilt = (await api.get(f"/v1/procurement-plans/{plan['id']}", headers=manager)).json()
    tomato = lines_by_slug(rebuilt)["tomato"]
    assert (tomato["committed_qty"], tomato["at_risk_qty"], tomato["suggested_qty"]) == (3, 0, 3.3)
    assert (tomato["order_qty"], tomato["edited"], tomato["edit_reason"]) == (
        5,
        True,
        "Vendor sells 5 kg crates",
    )
    assert (tomato["vendor"]["name"], tomato["unit_cost_paise"], tomato["amount_paise"]) == (
        "Kharghar Kisan FPO",
        3205,
        16025,
    )
    assert lines_by_slug(rebuilt)["carrot"]["order_qty"] == 1.1
    assert (rebuilt["version"], rebuilt["refreshed_at"] is not None) == (3, True)
    assert await db_session.scalar(select(func.count()).select_from(ProcurementPlan)) == 1

    # An order cancelled after the cut-off drops its unedited line; the edited one stays.
    await db_session.execute(
        update(Order).where(Order.status == "locked").values(status="cancelled")
    )
    await db_session.commit()
    event = OutboxEvent(
        event_type="intel.forecast_ready",
        payload={"hub_id": str(SEED_HUB_ID), "delivery_dates": ["2026-10-13", "2026-10-14"]},
    )
    await jobs.on_forecast_ready(db_session, event)
    await db_session.commit()
    after = (await api.get(f"/v1/procurement-plans/{plan['id']}", headers=manager)).json()
    assert list(lines_by_slug(after)) == ["tomato"]
    assert lines_by_slug(after)["tomato"]["committed_qty"] == 0
    assert await db_session.scalar(select(func.count()).select_from(ProcurementPlan)) == 1

    undo = await patch(api, manager, after, "tomato", order_qty=None)
    assert undo.status_code == 200, undo.text
    line = lines_by_slug(undo.json())["tomato"]
    assert (line["order_qty"], line["edited"], line["edit_reason"]) == (0, False, None)


@time_machine.travel(NOW, tick=False)
async def test_line_edits_need_the_version_and_a_reason_and_are_audited(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await order(db_session, "locked", {"tomato": 2, "palak": 3})
    api = clients()
    admin = await as_user(api, google, NEHA)
    other = (await api.post("/v1/admin/hubs", json=hub_body(), headers={**admin, **key()})).json()
    theirs = await api.post(
        f"/v1/hubs/{other['id']}/vendors",
        json={"name": "Elsewhere", "kind": "farmer"},
        headers={**admin, **key()},
    )
    manager = await as_user(api, google, VIKRAM)
    plan = (await build(api, manager)).json()
    vendors = await vendor_ids(db_session)
    await db_session.execute(
        update(Vendor).where(Vendor.name == "Patil Wholesale").values(active=False)
    )
    await db_session.commit()
    url = f"/v1/procurement-plans/{plan['id']}/lines/{lines_by_slug(plan)['tomato']['product_id']}"

    unversioned = await api.patch(url, json={"note": "x"}, headers=manager)
    assert unversioned.status_code == 428
    stale = await api.patch(url, json={"note": "x"}, headers={**manager, "If-Match": '"7"'})
    assert stale.json()["code"] == "request.version_mismatch"
    for body, code in (
        ({"order_qty": 3.0}, "hub_ops.reason_required"),
        ({"order_qty": 0.0001, "reason": "why not"}, "hub_ops.qty_invalid"),
        ({"order_qty": -1, "reason": "why not"}, "hub_ops.qty_invalid"),
        ({"vendor_id": vendors["patil"]}, "hub_ops.vendor_inactive"),
        ({"vendor_id": theirs.json()["id"]}, "hub_ops.vendor_not_found"),
        ({"vendor_id": str(uuid.uuid4())}, "hub_ops.vendor_not_found"),
        ({"reason": None}, "request.invalid"),
        ({"unit_cost_paise": 10_000_001}, "request.invalid"),
    ):
        refused = await patch(api, manager, plan, "tomato", **body)
        assert refused.json()["code"] == code, body
    bunches = await patch(api, manager, plan, "palak", order_qty=3.5, reason="half a bunch")
    assert bunches.json()["code"] == "hub_ops.qty_invalid"
    unknown = await api.patch(
        f"/v1/procurement-plans/{plan['id']}/lines/{uuid.uuid4()}",
        json={"note": "x"},
        headers={**manager, "If-Match": '"1"'},
    )
    assert unknown.json()["code"] == "hub_ops.line_not_found"

    done = await patch(
        api, manager, plan, "tomato", order_qty=3.0, reason="  Festival  ", note="Hybrid"
    )
    same = await patch(api, manager, done.json(), "tomato", order_qty=3.0, note="Hybrid")

    assert done.status_code == 200 and done.headers["ETag"] == '"2"'
    assert same.json()["version"] == 2  # nothing changed, so no reason was needed
    audit = (
        await db_session.execute(
            select(AuditEntry).where(AuditEntry.action == "procurement.line_updated")
        )
    ).scalars()
    assert [(a.before, a.after) for a in audit] == [
        (
            {"order_qty": 1.1, "edited": False, "edit_reason": None, "note": None},
            {
                "order_qty": 3.0,
                "edited": True,
                "edit_reason": "Festival",
                "note": "Hybrid",
                "reason": "Festival",
            },
        )
    ]
    await db_session.execute(update(ProcurementPlan).values(status="closed"))
    await db_session.commit()
    closed = await patch(api, manager, done.json(), "tomato", note="late")
    assert (closed.status_code, closed.json()["code"]) == (409, "hub_ops.plan_not_editable")
    rebuild = await build(api, manager)
    assert rebuild.json()["code"] == "hub_ops.plan_not_editable"
    job = await run_job(
        db_session, "build_procurement_plan", settings=SETTINGS, scheduled_for=AT_2110
    )
    assert job.summary == {"delivery_date": "2026-10-13", "hubs": 2, "closed": 1, "built": 1}


# --- purchase orders -----------------------------------------------------------------------------


@time_machine.travel(NOW, tick=False)
async def test_purchase_orders_per_vendor_are_drafts_until_sent_by_hand(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await order(db_session, "locked", {"tomato": 2, "palak": 3, "toor-dal": 2, "onion": 1})
    api = clients()
    manager = await as_user(api, google, VIKRAM)
    plan = (await build(api, manager)).json()
    vendors = await vendor_ids(db_session)
    for slug, body in (
        ("palak", {"vendor_id": vendors["kharghar"]}),
        ("toor-dal", {"vendor_id": vendors["patil"], "unit_cost_paise": 12_000}),
        ("tomato", {"order_qty": 0.1, "reason": "Stock left", "unit_cost_paise": 3205}),
        ("onion", {"vendor_id": None}),
    ):
        plan = (await patch(api, manager, plan, slug, **body)).json()
    generate = f"/v1/procurement-plans/{plan['id']}/purchase-orders"
    await db_session.commit()

    missing = await api.post(generate, headers={**manager, **key()})
    assert (missing.status_code, missing.json()["code"]) == (422, "hub_ops.vendor_missing")
    assert missing.json()["product_ids"] == [lines_by_slug(plan)["onion"]["product_id"]]
    plan = (await patch(api, manager, plan, "onion", vendor_id=vendors["shinde"])).json()
    once = {**manager, **key()}
    made = await api.post(generate, headers=once)
    replay = await api.post(generate, headers=once)
    remade = await api.post(generate, headers={**manager, **key()})

    assert made.status_code == 200, made.text
    assert replay.json() == made.json()
    pos = remade.json()["purchase_orders"]
    assert {po["id"] for po in pos}.isdisjoint(po["id"] for po in made.json()["purchase_orders"])
    assert [(po["vendor"]["name"], [x["slug"] for x in po["lines"]]) for po in pos] == [
        ("Kharghar Kisan FPO", ["palak"]),
        ("Patil Wholesale", ["toor-dal"]),
        ("Shinde Traders", ["onion", "tomato"]),
    ]
    shinde = pos[2]
    assert (shinde["status"], shinde["number"], shinde["stale"]) == ("draft", None, False)
    assert (shinde["total_paise"], shinde["unpriced_lines"]) == (321, 1)
    assert shinde["share_text"] == (
        "GharBasket Kharghar hub · PO draft\n"
        "Deliver by 04:00 on Tue 13 Oct 2026 to Plot 12, Sector 19, Kharghar, Navi Mumbai 410210\n"
        "1. Onion: 1.1 kg (no price agreed)\n"
        "2. Tomato: 0.1 kg @ ₹32.05/kg = ₹3.21\n"
        "Total ₹3.21 + 1 item at market price"
    )
    assert shinde["whatsapp_url"].startswith("https://wa.me/919800002001?text=GharBasket%20")
    assert pos[1]["total_paise"] == 13_200 and pos[1]["share_text"].endswith("Total ₹132.00")
    await db_session.execute(
        update(Vendor).where(Vendor.name == "Patil Wholesale").values(whatsapp=False)
    )
    plan = (await patch(api, manager, plan, "palak", note="Tie in tens")).json()
    await db_session.commit()

    old = (await api.get(f"/v1/purchase-orders/{shinde['id']}", headers=manager)).json()
    assert old["stale"] is True
    stale = await api.post(f"/v1/purchase-orders/{shinde['id']}/send", headers={**manager, **key()})
    assert (stale.status_code, stale.json()["code"]) == (409, "hub_ops.po_stale")
    pos = (await api.post(generate, headers={**manager, **key()})).json()["purchase_orders"]
    assert pos[1]["whatsapp_url"] is None  # the wholesaler takes no WhatsApp
    send = f"/v1/purchase-orders/{pos[2]['id']}/send"
    sent = await api.post(send, headers={**manager, **key()})
    second = await api.post(
        f"/v1/purchase-orders/{pos[0]['id']}/send", headers={**manager, **key()}
    )

    assert sent.status_code == 200, sent.text
    assert (sent.json()["status"], sent.json()["number"]) == ("sent", "KHARGHAR-261013-01")
    assert sent.json()["share_text"].startswith("GharBasket Kharghar hub · PO KHARGHAR-261013-01\n")
    assert second.json()["number"] == "KHARGHAR-261013-02"  # sending one never stales the others
    again = await api.post(send, headers={**manager, **key()})
    assert (again.status_code, again.json()["code"]) == (409, "hub_ops.po_not_draft")
    plan = (await api.get(f"/v1/procurement-plans/{plan['id']}", headers=manager)).json()
    assert plan["status"] == "ordered"
    assert lines_by_slug(plan)["tomato"]["purchase_order"] == {
        "id": pos[2]["id"],
        "number": "KHARGHAR-261013-01",
        "status": "sent",
    }
    ordered = await patch(api, manager, plan, "tomato", note="more")
    assert (ordered.status_code, ordered.json()["code"]) == (409, "hub_ops.line_ordered")
    sent_events = (
        await db_session.execute(
            select(OutboxEvent.payload).where(
                OutboxEvent.event_type == "hub_ops.purchase_order_sent"
            )
        )
    ).scalars()
    assert [(e["number"], e["lines_count"], e["total_paise"]) for e in sent_events] == [
        ("KHARGHAR-261013-01", 2, 321),
        ("KHARGHAR-261013-02", 1, 0),
    ]
    # Regenerating leaves sent lines alone: only the wholesaler's draft is left.
    left = (await api.post(generate, headers={**manager, **key()})).json()["purchase_orders"]
    assert [(po["vendor"]["name"], po["status"]) for po in left] == [
        ("Kharghar Kisan FPO", "sent"),
        ("Patil Wholesale", "draft"),
        ("Shinde Traders", "sent"),
    ]
    listed = await api.get(
        f"/v1/hubs/{SEED_HUB_ID}/purchase-orders",
        params={"date": "2026-10-13", "status": "sent"},
        headers=manager,
    )
    assert [po["number"] for po in listed.json()["items"]] == [
        "KHARGHAR-261013-02",
        "KHARGHAR-261013-01",
    ]

    for po in (pos[0], pos[2]):
        cancel = await api.post(
            f"/v1/purchase-orders/{po['id']}/cancel",
            json={"reason": "Rain at APMC"},
            headers={**manager, **key()},
        )
        assert (
            cancel.json()["status"] == "cancelled"
            and cancel.json()["cancel_reason"] == "Rain at APMC"
        )
    plan = (await api.get(f"/v1/procurement-plans/{plan['id']}", headers=manager)).json()
    assert plan["status"] == "draft"  # no sent PO left
    twice = await api.post(
        f"/v1/purchase-orders/{pos[2]['id']}/cancel",
        json={"reason": "again"},
        headers={**manager, **key()},
    )
    assert (twice.status_code, twice.json()["code"]) == (409, "hub_ops.po_not_cancellable")


@time_machine.travel(NOW, tick=False)
async def test_nothing_to_order_and_a_new_line_takes_the_last_vendor(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await order(db_session, "payment_failed", {"tomato": 2})
    api = clients()
    manager = await as_user(api, google, VIKRAM)
    plan = (await build(api, manager)).json()
    await db_session.commit()
    empty = await api.post(
        f"/v1/procurement-plans/{plan['id']}/purchase-orders", headers={**manager, **key()}
    )
    assert (empty.status_code, empty.json()["code"]) == (409, "hub_ops.nothing_to_order")

    vendors = await vendor_ids(db_session)
    await order(db_session, "locked", {"tomato": 2})
    plan = (await build(api, manager)).json()
    plan = (await patch(api, manager, plan, "tomato", vendor_id=vendors["kharghar"])).json()
    await api.post(
        f"/v1/procurement-plans/{plan['id']}/purchase-orders", headers={**manager, **key()}
    )
    await order(db_session, "locked", {"tomato": 1}, day=date(2026, 10, 14))
    tomorrow = (await build(api, manager, date="2026-10-14")).json()
    assert lines_by_slug(tomorrow)["tomato"]["vendor"]["name"] == "Kharghar Kisan FPO"


@time_machine.travel(NOW, tick=False)
async def test_hub_staff_reach_only_their_own_hub(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    admin = await as_user(api, google, NEHA)
    other = (await api.post("/v1/admin/hubs", json=hub_body(), headers={**admin, **key()})).json()
    rider = await db_session.scalar(select(User.id).where(User.phone_e164 == ARJUN))
    db_session.add(
        UserRole(
            user_id=rider, role="hub_manager", scope_type="hub", scope_id=uuid.UUID(other["id"])
        )
    )
    await order(db_session, "locked", {"tomato": 2})
    plan = (await build(api, admin)).json()
    po = (
        await api.post(
            f"/v1/procurement-plans/{plan['id']}/purchase-orders", headers={**admin, **key()}
        )
    ).json()["purchase_orders"][0]
    await db_session.commit()
    elsewhere = await as_user(api, google, ARJUN)

    assert (await api.get(PLANS, headers=elsewhere)).status_code == 403
    assert (await build(api, elsewhere)).status_code == 403
    assert (
        await api.get(f"/v1/hubs/{SEED_HUB_ID}/purchase-orders", headers=elsewhere)
    ).status_code == 403
    hidden = await api.get(f"/v1/procurement-plans/{plan['id']}", headers=elsewhere)
    assert (hidden.status_code, hidden.json()["code"]) == (404, "hub_ops.plan_not_found")
    edit = await patch(api, elsewhere, plan, "tomato", note="x")
    assert edit.json()["code"] == "hub_ops.plan_not_found"
    for path in (f"/v1/purchase-orders/{po['id']}", f"/v1/purchase-orders/{po['id']}/send"):
        method = "GET" if path.endswith(po["id"]) else "POST"
        refused = await api.request(method, path, headers={**elsewhere, **key()})
        assert refused.json()["code"] == "hub_ops.po_not_found"
    own = await api.post(
        f"/v1/hubs/{other['id']}/procurement-plans/build",
        json={"date": "2026-10-13"},
        headers={**elsewhere, **key()},
    )
    assert (own.status_code, own.json()["lines"]) == (200, [])

    owner = await as_user(api, google, SANJAY)  # the franchise owner reads, never writes
    assert (await api.get(f"/v1/purchase-orders/{po['id']}", headers=owner)).status_code == 200
    assert (await build(api, owner)).status_code == 403
    for phone in (KAVITA, PRIYA):
        assert (await api.get(PLANS, headers=await as_user(api, google, phone))).status_code == 403
