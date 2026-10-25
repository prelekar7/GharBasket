"""The 12:30 run and the surplus cascade (M6 §3.4, §3.5): the draw-down of packing (first expiry
first out, paid lines only, unlotted when no lot covers it, once), closing the day's buying, new
surplus offered to kitchens and the past binned, unsettled offers moving on (kitchen, NGO, bin),
settling offers, the job, permissions and events."""

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx2
import pytest
import time_machine
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.hub_ops import api as hub_ops_api
from gharbasket.modules.hub_ops import procurement, receiving, surplus
from gharbasket.modules.hub_ops.models import (
    InventoryLot,
    PackingLine,
    PackingTask,
    StockMove,
    SurplusOffer,
    WasteLog,
)
from gharbasket.modules.identity.models import User
from gharbasket.modules.partners.models import Partner
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.jobs import run_job
from gharbasket.platform.models import OutboxEvent
from gharbasket.tools.seed import SEED_HUB_ID

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .conftest import KAVITA, NEHA, PRIYA, SANJAY, VIKRAM, as_user, hub_body, key
from .test_procurement import ARJUN, SETTINGS, world
from .test_receiving import line, receive, sent_po, slugs
from .test_waste import grant

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("world")]
__all__ = ["world"]  # the fixture, imported so this module can use it

HUB = f"/v1/hubs/{SEED_HUB_ID}"
RUN = f"{HUB}/surplus/run"
OFFERS = f"{HUB}/surplus-offers"
IST = "+05:30"
TODAY = date(2026, 10, 13)  # Tuesday, the delivery day
AT_1230 = datetime(2026, 10, 13, 7, 0, tzinfo=UTC)  # 12:30 IST
EMPTY = {"drawn_tasks": 0, "closed_purchase_orders": 0, "offers": 0, "binned": 0}


def ist(day: str, clock: str = "12:30") -> datetime:
    return datetime.fromisoformat(f"{day}T{clock}{IST}")


async def make_lot(
    db: AsyncSession,
    slug: str,
    qty: float,
    *,
    best_before: datetime,
    grade: str = "A",
    source: str = "receipt",
    received_at: datetime | None = None,
    hub_id: uuid.UUID = SEED_HUB_ID,
) -> uuid.UUID:
    """A lot with its `receive` (or `return`) move, committed."""
    product_id = uuid.UUID((await slugs(db, slug))[slug])
    product = (await catalog_api.stock_products(db, [product_id]))[product_id]
    unit = procurement.OPS_UNIT[product.step_unit][0]
    lot = InventoryLot(
        hub_id=hub_id,
        product_id=product_id,
        unit=unit,
        source=source,
        order_id=uuid.uuid4() if source == "returned" else None,
        grade=grade,
        qty_in=Decimal(str(qty)),
        qty_on_hand=Decimal(0),
        received_at=received_at or ist("2026-10-12", "04:00"),
        best_before=best_before,
    )
    db.add(lot)
    await db.flush()
    await receiving.move(
        db,
        hub_id=hub_id,
        product_id=product_id,
        unit=unit,
        kind="receive" if source == "receipt" else "return",
        ref_id=uuid.uuid4(),
        qty=Decimal(str(qty)),
        lot=lot,
    )
    await db.commit()
    return lot.id


async def make_task(
    db: AsyncSession,
    status: str,
    lines: list[dict[str, Any]],
    *,
    day: date = TODAY,
    packed_at: datetime | None = None,
) -> uuid.UUID:
    task = PackingTask(
        hub_id=SEED_HUB_ID,
        order_id=uuid.uuid4(),
        order_kind="box",
        delivery_date=day,
        slot_id=uuid.uuid4(),
        society_id=uuid.uuid4(),
        seq=1,
        status=status,
        packed_at=packed_at or (ist(day.isoformat(), "06:00") if status == "packed" else None),
    )
    db.add(task)
    await db.flush()
    db.add_all(PackingLine(task_id=task.id, **x) for x in lines)
    await db.commit()
    return task.id


async def paid_line(
    db: AsyncSession, slug: str, qty: int, picked: int, substitute: str | None = None, **more: Any
) -> dict[str, Any]:
    ids = await slugs(db, slug, *([substitute] if substitute else []))
    product_id = uuid.UUID(ids[slug])
    product = (await catalog_api.stock_products(db, [product_id]))[product_id]
    return {
        "product_id": product_id,
        "step_qty": product.step_qty,
        "step_unit": product.step_unit,
        "qty": qty,
        "picked_qty": picked,
        "substitute_product_id": uuid.UUID(ids[substitute]) if substitute else None,
        **more,
    }


async def run(api: httpx2.AsyncClient, auth: dict[str, str], **headers: str) -> httpx2.Response:
    return await api.post(RUN, headers={**auth, **(headers or key())})


async def settle(
    api: httpx2.AsyncClient,
    auth: dict[str, str],
    offer_id: str,
    body: dict[str, Any],
    **headers: str,
) -> httpx2.Response:
    return await api.post(
        f"/v1/surplus-offers/{offer_id}/settle", json=body, headers={**auth, **(headers or key())}
    )


async def lot_row(db: AsyncSession, lot_id: uuid.UUID) -> InventoryLot:
    lot = await db.get(InventoryLot, lot_id, populate_existing=True)
    assert lot is not None
    return lot


async def moves(db: AsyncSession, **where: Any) -> list[tuple[Any, ...]]:
    stmt = select(StockMove.kind, StockMove.lot_id, StockMove.product_id, StockMove.qty)
    for name, value in where.items():
        stmt = stmt.where(getattr(StockMove, name) == value)
    return sorted((tuple(row) for row in (await db.execute(stmt)).all()), key=str)


async def payloads(db: AsyncSession, event: str) -> list[dict[str, Any]]:
    rows = await db.scalars(
        select(OutboxEvent.payload).where(OutboxEvent.event_type == event).order_by(OutboxEvent.id)
    )
    return list(rows)


async def kitchen(
    db: AsyncSession,
    name: str = "Lata's Kitchen",
    *,
    hub_id: uuid.UUID = SEED_HUB_ID,
    kind: str = "kitchen",
    status: str = "active",
) -> uuid.UUID:
    partner = Partner(
        user_id=uuid.uuid4(),
        kind=kind,
        hub_id=hub_id,
        display_name=name,
        status=status,
        verified_at=datetime(2026, 10, 1, tzinfo=UTC),
        **(
            {"suspended_at": datetime(2026, 10, 2, tzinfo=UTC), "suspend_reason_code": "quality"}
            if status == "suspended"
            else {}
        ),
    )
    db.add(partner)
    await db.commit()
    return partner.id


def code(response: httpx2.Response) -> tuple[int, str]:
    return response.status_code, response.json()["code"]


# --- the draw-down (§3.4) ------------------------------------------------------------------------


@time_machine.travel(AT_1230, tick=False)
async def test_the_draw_down_is_first_expiry_first_paid_lines_only_unlotted_and_once(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    manager = await as_user(api, google, VIKRAM)
    later = await make_lot(db_session, "tomato", 3, best_before=ist("2026-10-20"))
    first = await make_lot(db_session, "tomato", 2, grade="B", best_before=ist("2026-10-18"))
    young = await make_lot(
        db_session,
        "tomato",
        2,
        best_before=ist("2026-10-20"),
        received_at=ist("2026-10-12", "05:00"),  # same best_before, received later: after `later`
    )
    flower = await make_lot(db_session, "cauliflower", 5, best_before=ist("2026-10-25"))
    ids = {
        k: uuid.UUID(v)
        for k, v in (await slugs(db_session, "tomato", "onion", "cauliflower", "bhindi")).items()
    }
    # 8 steps of 500 g tomato (4 kg) and 2 kg onion (no lot) paid; a kept tomato rides free.
    packed = await make_task(
        db_session,
        "packed",
        [
            await paid_line(db_session, "tomato", 8, 8),
            await paid_line(db_session, "tomato", 2, 2, carried=True),
            await paid_line(db_session, "onion", 3, 2, short_qty=1),
        ],
    )
    # Yesterday's task not drawn yet: bhindi (250 g steps) replaced by 2 cauliflowers, its own step.
    yesterday = await make_task(
        db_session,
        "packed",
        [await paid_line(db_session, "bhindi", 2, 2, "cauliflower")],
        day=TODAY - timedelta(days=1),
    )
    queued = await make_task(db_session, "queued", [await paid_line(db_session, "tomato", 4, 0)])
    tomorrow = await make_task(
        db_session,
        "packed",
        [await paid_line(db_session, "tomato", 2, 2)],
        day=TODAY + timedelta(1),
    )

    before = (await api.get(f"{HUB}/inventory", headers=manager)).json()
    by_slug = {x["slug"]: x for x in before["items"]}
    assert by_slug["tomato"]["packed_not_drawn_qty"] == 5.0  # 4 kg today + 1 kg tomorrow
    assert by_slug["cauliflower"]["packed_not_drawn_qty"] == 2.0

    done = await run(api, manager)

    assert done.status_code == 200, done.text
    assert done.json() == {**EMPTY, "drawn_tasks": 2}
    # Earliest best_before first (the B lot), then by received_at: `later` before `young`.
    assert [(await lot_row(db_session, x)).qty_on_hand for x in (first, later, young, flower)] == [
        0,
        1,
        2,
        3,
    ]
    assert await moves(db_session, kind="pack", ref_id=packed) == sorted(
        [
            ("pack", first, ids["tomato"], Decimal(-2)),
            ("pack", later, ids["tomato"], Decimal(-2)),
            ("pack", None, ids["onion"], Decimal(-2)),  # unlotted: no onion was received
        ],
        key=str,
    )
    assert await moves(db_session, kind="pack", ref_id=yesterday) == [
        ("pack", flower, ids["cauliflower"], Decimal(-2))
    ]
    assert await moves(db_session, ref_id=queued) == []
    assert await moves(db_session, ref_id=tomorrow) == []
    drawn = {
        t.id: t.drawn_at
        for t in await db_session.scalars(
            select(PackingTask).execution_options(populate_existing=True)
        )
    }
    assert drawn[packed] == drawn[yesterday] == AT_1230
    assert drawn[queued] is None and drawn[tomorrow] is None
    after = {
        x["slug"]: x for x in (await api.get(f"{HUB}/inventory", headers=manager)).json()["items"]
    }
    assert after["tomato"]["packed_not_drawn_qty"] == 1.0  # tomorrow's task only
    assert after["cauliflower"]["packed_not_drawn_qty"] == 0.0

    # Once: a second run (and the same key) moves nothing.
    count = await db_session.scalar(select(func.count()).select_from(StockMove))
    again = await run(api, manager)
    assert again.status_code == 200 and again.json() == EMPTY
    assert await db_session.scalar(select(func.count()).select_from(StockMove)) == count
    for lot_id in (first, later, young, flower):
        lot = await lot_row(db_session, lot_id)
        total = await db_session.scalar(
            select(func.sum(StockMove.qty)).where(StockMove.lot_id == lot_id)
        )
        assert total is not None and lot.qty_on_hand == total >= 0


# --- closing the day's buying --------------------------------------------------------------------


async def test_the_run_closes_the_days_purchase_orders_and_plan(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    with time_machine.travel(datetime(2026, 10, 12, 16, 0, tzinfo=UTC), tick=False):  # T-1 21:30
        manager = await as_user(api, google, VIKRAM)
        po = await sent_po(
            api, manager, db_session, {"tomato": 4, "onion": 2, "palak": 10}, fpo=("palak",)
        )
        [fpo] = po["drafts"]
        sent = await api.post(f"/v1/purchase-orders/{fpo['id']}/send", headers={**manager, **key()})
        assert sent.status_code == 200, sent.text
    ids = await slugs(db_session, "tomato")
    with time_machine.travel(datetime(2026, 10, 12, 22, 45, tzinfo=UTC), tick=False):  # 04:15
        manager = await as_user(api, google, VIKRAM)
        got = await receive(api, manager, line(ids["tomato"], 2.2), purchase_order_id=po["id"])
        assert got.status_code == 201, got.text

    with time_machine.travel(AT_1230, tick=False):
        manager = await as_user(api, google, VIKRAM)
        done = await run(api, manager)
        assert done.status_code == 200, done.text
        assert done.json() == {**EMPTY, "closed_purchase_orders": 2}
        shinde = (await api.get(f"/v1/purchase-orders/{po['id']}", headers=manager)).json()
        kisan = (await api.get(f"/v1/purchase-orders/{fpo['id']}", headers=manager)).json()
        plan = (
            await api.get(
                f"{HUB}/procurement-plans", params={"date": "2026-10-13"}, headers=manager
            )
        ).json()
        again = await run(api, manager)
        rebuilt = await api.post(
            f"{HUB}/procurement-plans/build",
            json={"date": "2026-10-13"},
            headers={**manager, **key()},
        )

    assert (shinde["status"], shinde["cancel_reason"]) == ("received", None)
    assert shinde["closed_at"] is not None
    assert (kisan["status"], kisan["cancel_reason"]) == ("cancelled", "not_delivered")
    assert plan["status"] == "closed"
    assert again.json() == EMPTY
    assert code(rebuilt) == (409, "hub_ops.plan_not_editable")


# --- new surplus and the cascade -----------------------------------------------------------------


@time_machine.travel(AT_1230, tick=False)
async def test_new_surplus_is_offered_to_kitchens_and_the_past_is_binned_once(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    manager = await as_user(api, google, VIKRAM)
    end_of_tomorrow = ist("2026-10-15", "00:00")
    grade_c = await make_lot(db_session, "tomato", 6, grade="C", best_before=ist("2026-10-30"))
    short = await make_lot(db_session, "onion", 4, best_before=end_of_tomorrow)
    lasts = await make_lot(
        db_session, "onion", 3, best_before=end_of_tomorrow + timedelta(minutes=1)
    )
    past = await make_lot(db_session, "palak", 5, grade="B", best_before=ist("2026-10-13", "10:00"))
    empty = await make_lot(db_session, "tomato", 1, best_before=ist("2026-10-13", "09:00"))
    await db_session.execute(
        update(InventoryLot).where(InventoryLot.id == empty).values(qty_on_hand=0)
    )
    await db_session.commit()

    done = await run(api, manager)

    assert done.status_code == 200, done.text
    assert done.json() == {**EMPTY, "offers": 2, "binned": 1}
    for lot_id, left in ((grade_c, 0), (short, 0), (lasts, 3), (past, 0)):
        assert (await lot_row(db_session, lot_id)).qty_on_hand == left
    listed = (await api.get(OFFERS, headers=manager)).json()["items"]
    offers = {x["lot_id"]: x for x in listed}
    assert set(offers) == {str(grade_c), str(short)}
    tomato = offers[str(grade_c)]
    assert tomato.pop("id") and tomato.pop("created_at")
    product = (await slugs(db_session, "tomato"))["tomato"]
    assert tomato == {
        "hub_id": str(SEED_HUB_ID),
        "lot_id": str(grade_c),
        "product_id": product,
        "slug": "tomato",
        "name": tomato["name"],
        "unit": "kg",
        "grade": "C",
        "best_before": "2026-10-30T07:00:00Z",
        "qty": 6.0,
        "offered_to": "kitchen",
        "status": "open",
        "taken_qty": None,
        "taken_by": None,
        "previous_offer_id": None,
        "expires_at": "2026-10-14T07:00:00Z",  # the next day's 12:30 IST
        "settled_at": None,
    }
    assert offers[str(short)]["qty"] == 4.0 and offers[str(short)]["grade"] == "A"
    offer_moves = await moves(db_session, kind="offer")
    assert sorted((m[1], m[3]) for m in offer_moves) == sorted(
        [(grade_c, Decimal(-6)), (short, Decimal(-4))]
    )
    [binned] = (await db_session.scalars(select(WasteLog))).all()
    assert (binned.lot_id, binned.qty, binned.reason, binned.cascade_to) == (
        past,
        5,
        "expired",
        "bin",
    )
    assert (binned.offer_id, binned.logged_by, binned.kg, binned.at) == (
        None,
        None,
        Decimal("0.5"),
        AT_1230,
    )
    assert await moves(db_session, kind="waste", ref_id=binned.id) == [
        ("waste", past, binned.product_id, Decimal(-5))
    ]
    offered = await payloads(db_session, "hub_ops.surplus_offered")
    assert sorted(x["lot_id"] for x in offered) == sorted([str(grade_c), str(short)])
    assert offered[0].keys() == {
        "offer_id",
        "hub_id",
        "lot_id",
        "product_id",
        "qty",
        "unit",
        "grade",
        "best_before",
        "offered_to",
        "expires_at",
    }
    by_lot = {x["lot_id"]: x for x in offered}
    assert by_lot[str(grade_c)] | {"offer_id": None} == {
        "offer_id": None,
        "hub_id": str(SEED_HUB_ID),
        "lot_id": str(grade_c),
        "product_id": product,
        "qty": 6.0,
        "unit": "kg",
        "grade": "C",
        "best_before": "2026-10-30T07:00:00+00:00",
        "offered_to": "kitchen",
        "expires_at": "2026-10-14T07:00:00+00:00",
    }
    logged = await payloads(db_session, "hub_ops.waste_logged")
    assert [(x["reason"], x["cascade_to"], x["qty"]) for x in logged] == [("expired", "bin", 5.0)]

    # A second run the same day writes nothing.
    counts = [
        await db_session.scalar(select(func.count()).select_from(t))
        for t in (SurplusOffer, WasteLog, StockMove, OutboxEvent)
    ]
    again = await run(api, manager)
    assert again.json() == EMPTY
    assert counts == [
        await db_session.scalar(select(func.count()).select_from(t))
        for t in (SurplusOffer, WasteLog, StockMove, OutboxEvent)
    ]
    # The list reads one IST day and filters by status.
    assert (await api.get(OFFERS, params={"status": "taken"}, headers=manager)).json() == {
        "items": []
    }
    yesterday = await api.get(OFFERS, params={"date": "2026-10-12"}, headers=manager)
    assert yesterday.json() == {"items": []}
    far = await api.get(OFFERS, params={"date": "2026-10-01"}, headers=manager)
    assert code(far) == (422, "hub_ops.date_out_of_range")


async def test_unsettled_offers_move_on_from_kitchen_to_ngo_to_bin_by_the_job(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    """Three days of the 12:30 job: offers nobody settles move on, a returned lot is binned as
    `returned`, and a lot whose best_before passes while offered goes straight to the bin."""
    tomato = await make_lot(db_session, "tomato", 6, grade="C", best_before=ist("2026-10-30"))
    returned = await make_lot(
        db_session, "onion", 2, grade="C", source="returned", best_before=ist("2026-10-30")
    )
    soon = await make_lot(db_session, "palak", 3, best_before=ist("2026-10-14", "09:00"))
    jobs = []
    for day in ("2026-10-13", "2026-10-14", "2026-10-14", "2026-10-15", "2026-10-16"):
        jobs.append(
            await run_job(db_session, "surplus_cascade", settings=SETTINGS, scheduled_for=ist(day))
        )
    summaries = [j.summary for j in jobs]
    assert [j.status for j in jobs] == ["ok"] * 5
    assert summaries == [
        {"date": "2026-10-13", "hubs": 1, **EMPTY, "offers": 3},
        {"date": "2026-10-14", "hubs": 1, **EMPTY, "offers": 2, "binned": 1},  # palak expired
        {"date": "2026-10-14", "hubs": 1, **EMPTY},  # the same day again
        {"date": "2026-10-15", "hubs": 1, **EMPTY, "binned": 2},
        {"date": "2026-10-16", "hubs": 1, **EMPTY},
    ]
    offers = list(
        await db_session.scalars(
            select(SurplusOffer)
            .order_by(SurplusOffer.created_at, SurplusOffer.id)
            .execution_options(populate_existing=True)
        )
    )
    by_lot: dict[uuid.UUID, list[SurplusOffer]] = {}
    for offer in offers:
        by_lot.setdefault(offer.lot_id, []).append(offer)
    for lot_id in (tomato, returned):
        kitchen_offer, ngo_offer = by_lot[lot_id]
        assert (kitchen_offer.offered_to, kitchen_offer.status) == ("kitchen", "expired")
        assert (ngo_offer.offered_to, ngo_offer.status) == ("ngo", "expired")
        assert ngo_offer.previous_offer_id == kitchen_offer.id
        assert kitchen_offer.settled_at == ist("2026-10-14") and kitchen_offer.settled_by is None
        assert ngo_offer.expires_at == ist("2026-10-15")
    [palak] = by_lot[soon]
    assert (palak.status, palak.offered_to) == ("expired", "kitchen")
    logs = {
        log.lot_id: log
        for log in await db_session.scalars(
            select(WasteLog).execution_options(populate_existing=True)
        )
    }
    assert (logs[soon].reason, logs[soon].cascade_to, logs[soon].offer_id) == (
        "expired",
        "bin",
        palak.id,
    )
    assert logs[soon].at == ist("2026-10-14")
    assert (logs[tomato].reason, logs[tomato].offer_id) == ("surplus", by_lot[tomato][1].id)
    assert (logs[returned].reason, logs[returned].offer_id) == ("returned", by_lot[returned][1].id)
    assert {log.cascade_to for log in logs.values()} == {"bin"}
    # Stock left each lot once, by its first offer; what followed moved no stock.
    assert len(await moves(db_session, kind="offer")) == 3
    assert await moves(db_session, kind="waste") == []
    # The waste summary counts the bin as waste on the day it was binned.
    days = await hub_ops_api.waste_summary(
        db_session, SEED_HUB_ID, date(2026, 10, 14), date(2026, 10, 15)
    )
    assert [x.wasted_kg for x in days] == [0.3, 8.0]  # 3 bunches of palak; 6 + 2 kg


async def test_the_job_runs_every_active_hub_and_lists_one_that_fails(
    clients: ClientFactory,
    google: FakeGoogle,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    api = clients()
    with time_machine.travel(AT_1230, tick=False):
        admin = await as_user(api, google, NEHA)
        other = (
            await api.post("/v1/admin/hubs", json=hub_body(), headers={**admin, **key()})
        ).json()
        paused = (
            await api.post("/v1/admin/hubs", json=hub_body("panvel"), headers={**admin, **key()})
        ).json()
        await api.patch(
            f"/v1/admin/hubs/{paused['id']}",
            json={"status": "paused"},
            headers={**admin, "If-Match": '"1"'},
        )
    await make_lot(db_session, "tomato", 1, grade="C", best_before=ist("2026-10-30"))
    await make_lot(
        db_session,
        "tomato",
        1,
        grade="C",
        best_before=ist("2026-10-30"),
        hub_id=uuid.UUID(other["id"]),
    )
    await make_lot(
        db_session,
        "tomato",
        1,
        grade="C",
        best_before=ist("2026-10-30"),
        hub_id=uuid.UUID(paused["id"]),
    )
    real = surplus.run

    async def broken(session: AsyncSession, hub_id: uuid.UUID, now: datetime) -> dict[str, int]:
        if hub_id == uuid.UUID(other["id"]):
            await real(session, hub_id, now)
            raise RuntimeError("boom")
        return await real(session, hub_id, now)

    monkeypatch.setattr(surplus, "run", broken)
    job = await run_job(db_session, "surplus_cascade", settings=SETTINGS, scheduled_for=AT_1230)

    assert job.status == "ok"
    assert job.summary == {
        "date": "2026-10-13",
        "hubs": 2,
        **EMPTY,
        "offers": 1,
        "failed_hubs": [other["id"]],
    }
    hubs = (await db_session.scalars(select(SurplusOffer.hub_id))).all()
    assert hubs == [SEED_HUB_ID]  # the failed hub's offer was rolled back; the paused hub has none


# --- settling ------------------------------------------------------------------------------------


@time_machine.travel(AT_1230, tick=False)
async def test_offers_are_taken_in_full_or_in_part_or_declined_and_the_rest_moves_on(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    manager = await as_user(api, google, VIKRAM)
    lata = await kitchen(db_session)
    for slug, qty in (("tomato", 6), ("onion", 5), ("palak", 4)):
        await make_lot(db_session, slug, qty, grade="C", best_before=ist("2026-10-30"))
    assert (await run(api, manager)).json()["offers"] == 3
    offers = {x["slug"]: x for x in (await api.get(OFFERS, headers=manager)).json()["items"]}
    vikram = await db_session.scalar(select(User.id).where(User.phone_e164 == VIKRAM))
    taker = {"kind": "kitchen", "partner_id": str(lata)}
    once = {"Idempotency-Key": uuid.uuid4().hex}
    body = {"outcome": "taken", "qty": 4, "taken_by": taker, "note": "  Lunch  rush "}

    part = await settle(api, manager, offers["tomato"]["id"], body, **once)
    replay = await settle(api, manager, offers["tomato"]["id"], body, **once)

    assert part.status_code == 200, part.text
    assert replay.json() == part.json()
    out = part.json()
    assert set(out) == {"offer", "next", "waste_logs"}
    assert (out["offer"]["status"], out["offer"]["taken_qty"]) == ("taken", 4.0)
    assert out["offer"]["taken_by"] == {**taker, "name": "Lata's Kitchen"}
    assert out["offer"]["settled_at"] == "2026-10-13T07:00:00Z"
    nxt = out["next"]
    assert (nxt["offered_to"], nxt["qty"], nxt["status"]) == ("ngo", 2.0, "open")
    assert nxt["previous_offer_id"] == offers["tomato"]["id"]
    assert nxt["expires_at"] == "2026-10-14T07:00:00Z"
    [log] = out["waste_logs"]
    assert (log["qty"], log["kg"], log["reason"], log["cascade_to"]) == (
        4.0,
        4.0,
        "surplus",
        "kitchen",
    )
    assert log["taken_by"] == {**taker, "name": "Lata's Kitchen"}
    assert log["offer_id"] == offers["tomato"]["id"] and log["note"] == "Lunch rush"
    assert log["logged_by"]["user_id"] == str(vikram)
    again = await settle(api, manager, offers["tomato"]["id"], body)
    assert code(again) == (409, "hub_ops.offer_settled")
    assert again.json()["current_status"] == "taken"

    full = await settle(
        api, manager, offers["onion"]["id"], {"outcome": "taken", "taken_by": taker}
    )
    assert full.status_code == 200, full.text
    assert full.json()["next"] is None and full.json()["offer"]["taken_qty"] == 5.0
    assert [x["qty"] for x in full.json()["waste_logs"]] == [5.0]

    declined = await settle(api, manager, offers["palak"]["id"], {"outcome": "declined"})
    assert declined.status_code == 200, declined.text
    assert declined.json()["offer"]["status"] == "declined"
    assert declined.json()["offer"]["taken_qty"] is None
    assert declined.json()["waste_logs"] == []
    ngo_palak = declined.json()["next"]
    assert (ngo_palak["offered_to"], ngo_palak["qty"]) == ("ngo", 4.0)
    binned = await settle(api, manager, ngo_palak["id"], {"outcome": "declined", "note": "Closed"})
    assert binned.json()["next"] is None
    [bin_log] = binned.json()["waste_logs"]
    assert (bin_log["reason"], bin_log["cascade_to"], bin_log["kg"]) == ("surplus", "bin", 0.4)

    ngo = {"kind": "ngo", "name": "  Annadaan   Seva "}
    shared = await settle(
        api, manager, nxt["id"], {"outcome": "taken", "qty": 1.5, "taken_by": ngo}
    )
    assert shared.status_code == 200, shared.text
    assert shared.json()["next"] is None  # the rest of an NGO offer goes to the bin
    assert [(x["cascade_to"], x["qty"], x["taken_by"]) for x in shared.json()["waste_logs"]] == [
        ("ngo", 1.5, {"kind": "ngo", "name": "Annadaan Seva"}),
        ("bin", 0.5, None),
    ]

    # Settling moves no stock; the KPI counts the bin as waste and the rest as rescued.
    assert await moves(db_session, kind="waste") == []
    [today] = await hub_ops_api.waste_summary(db_session, SEED_HUB_ID, TODAY, TODAY)
    assert (today.wasted_kg, today.rescued_kg) == (0.9, {"kitchen": 9.0, "ngo": 1.5})
    logged = await payloads(db_session, "hub_ops.waste_logged")
    assert sorted(x["cascade_to"] for x in logged) == ["bin", "bin", "kitchen", "kitchen", "ngo"]
    offered = await payloads(db_session, "hub_ops.surplus_offered")
    assert [x["offered_to"] for x in offered] == ["kitchen"] * 3 + ["ngo"] * 2


@time_machine.travel(AT_1230, tick=False)
async def test_settling_refuses_bad_takers_and_quantities(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    admin = await as_user(api, google, NEHA)
    manager = await as_user(api, google, VIKRAM)
    other = (await api.post("/v1/admin/hubs", json=hub_body(), headers={**admin, **key()})).json()
    lata = await kitchen(db_session)
    elsewhere = await kitchen(db_session, "Vashi Kitchen", hub_id=uuid.UUID(other["id"]))
    paused = await kitchen(db_session, "Paused Kitchen", status="paused")
    captain = await kitchen(db_session, "A Captain", kind="captain")
    await make_lot(db_session, "palak", 4, grade="C", best_before=ist("2026-10-30"))
    await make_lot(db_session, "tomato", 2, grade="C", best_before=ist("2026-10-30"))
    assert (await run(api, manager)).status_code == 200
    offers = {x["slug"]: x for x in (await api.get(OFFERS, headers=manager)).json()["items"]}
    palak = offers["palak"]["id"]

    def kitchen_of(partner: uuid.UUID) -> dict[str, Any]:
        return {"outcome": "taken", "taken_by": {"kind": "kitchen", "partner_id": str(partner)}}

    for partner in (elsewhere, paused, captain, uuid.uuid4()):
        refused = await settle(api, manager, palak, kitchen_of(partner))
        assert code(refused) == (422, "hub_ops.taker_invalid")
        assert refused.json()["reason"] == "not_a_kitchen"
    for body, reason in (
        ({"outcome": "taken"}, "missing"),
        ({"outcome": "taken", "taken_by": {"kind": "ngo", "name": "Annadaan"}}, "kind_mismatch"),
        ({"outcome": "declined", "taken_by": {"kind": "ngo", "name": "Annadaan"}}, "declined"),
    ):
        refused = await settle(api, manager, palak, body)
        assert code(refused) == (422, "hub_ops.taker_invalid"), body
        assert refused.json()["reason"] == reason
    assert code(await settle(api, manager, palak, {**kitchen_of(lata), "qty": 5})) == (
        422,
        "hub_ops.qty_invalid",
    )  # above the offer
    assert code(await settle(api, manager, palak, {**kitchen_of(lata), "qty": 1.5})) == (
        422,
        "hub_ops.qty_invalid",
    )  # whole bunches
    for bad in (
        {**kitchen_of(lata), "qty": 0},
        {"outcome": "kept"},
        {"outcome": "taken", "taken_by": {"kind": "ngo", "name": "   "}},
        {"outcome": "taken", "taken_by": {"kind": "ngo", "name": "x" * 81}},
        {"outcome": "declined", "note": "x" * 301},
    ):
        assert code(await settle(api, manager, palak, bad)) == (422, "request.invalid"), bad
    assert code(await settle(api, manager, str(uuid.uuid4()), {"outcome": "declined"})) == (
        404,
        "hub_ops.offer_not_found",
    )
    missing = await api.post(
        f"/v1/surplus-offers/{palak}/settle", json={"outcome": "declined"}, headers=manager
    )
    assert code(missing) == (400, "request.idempotency_key_required")
    reused = {"Idempotency-Key": uuid.uuid4().hex}
    assert (await settle(api, manager, palak, kitchen_of(lata), **reused)).status_code == 200
    changed = await settle(api, manager, palak, {"outcome": "declined"}, **reused)
    assert code(changed) == (422, "request.idempotency_mismatch")
    assert (
        await db_session.scalar(
            select(SurplusOffer.status).where(SurplusOffer.id == uuid.UUID(palak))
        )
        == "taken"
    )

    # Through the facade (M8 and M9 later), with the same rules.
    tomato = uuid.UUID(offers["tomato"]["id"])
    with pytest.raises(ProblemError) as refused_facade:
        await hub_ops_api.settle_surplus(db_session, tomato, "taken", qty=3)
    assert refused_facade.value.code == "hub_ops.qty_invalid"
    result = await hub_ops_api.settle_surplus(
        db_session, tomato, "taken", qty=0.5, taken_by=hub_ops_api.Taker("kitchen", lata)
    )
    await db_session.commit()
    assert (result.offer.status, result.offer.taken_qty) == ("taken", 0.5)
    assert result.next is not None and (result.next.offered_to, result.next.qty) == ("ngo", 1.5)
    assert [(x.cascade_to, x.qty) for x in result.waste_logs] == [("kitchen", 0.5)]


@time_machine.travel(AT_1230, tick=False)
async def test_the_database_keeps_one_follower_and_one_log_per_destination(
    db_session: AsyncSession,
) -> None:
    lot = await make_lot(db_session, "tomato", 2, grade="C", best_before=ist("2026-10-30"))
    product = (await lot_row(db_session, lot)).product_id
    first = SurplusOffer(
        hub_id=SEED_HUB_ID,
        lot_id=lot,
        product_id=product,
        qty=Decimal(2),
        unit="kg",
        offered_to="kitchen",
        expires_at=ist("2026-10-14"),
    )
    db_session.add(first)
    await db_session.commit()

    def follower() -> SurplusOffer:
        return SurplusOffer(
            hub_id=SEED_HUB_ID,
            lot_id=lot,
            product_id=product,
            qty=Decimal(1),
            unit="kg",
            offered_to="ngo",
            previous_offer_id=first.id,
            expires_at=ist("2026-10-14"),
        )

    def bin_log() -> WasteLog:
        return WasteLog(
            hub_id=SEED_HUB_ID,
            lot_id=lot,
            product_id=product,
            qty=Decimal(1),
            unit="kg",
            reason="surplus",
            cascade_to="bin",
            offer_id=first.id,
        )

    for rows in ((follower(), follower()), (bin_log(), bin_log())):
        async with db_session.begin_nested():
            db_session.add(rows[0])
        with pytest.raises(IntegrityError):
            async with db_session.begin_nested():
                db_session.add(rows[1])
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.execute(
                update(SurplusOffer).where(SurplusOffer.id == first.id).values(taken_qty=3)
            )


# --- who may run, settle and read ----------------------------------------------------------------


@time_machine.travel(AT_1230, tick=False)
async def test_who_may_run_settle_and_read(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    admin = await as_user(api, google, NEHA)
    manager = await as_user(api, google, VIKRAM)
    other = (await api.post("/v1/admin/hubs", json=hub_body(), headers={**admin, **key()})).json()
    await grant(db_session, ARJUN, "hub_manager", uuid.UUID(other["id"]))
    await make_lot(db_session, "tomato", 2, grade="C", best_before=ist("2026-10-30"))
    await make_lot(db_session, "onion", 2, grade="C", best_before=ist("2026-10-30"))

    packer = await as_user(api, google, KAVITA)
    owner = await as_user(api, google, SANJAY)
    customer = await as_user(api, google, PRIYA)
    elsewhere = await as_user(api, google, ARJUN)
    for who in (packer, owner, customer, elsewhere):
        assert (await run(api, who)).status_code == 403
    assert (await api.post(RUN, headers=key())).status_code == 401
    assert (await run(api, admin)).json()["offers"] == 2  # the admin runs it too
    assert code(
        await api.post(f"/v1/hubs/{uuid.uuid4()}/surplus/run", headers={**admin, **key()})
    ) == (404, "hub_ops.hub_not_found")

    for who in (packer, owner, admin, manager):
        assert len((await api.get(OFFERS, headers=who)).json()["items"]) == 2
    assert (await api.get(OFFERS, headers=customer)).status_code == 403
    assert (await api.get(OFFERS, headers=elsewhere)).status_code == 403
    assert (await api.get(OFFERS)).status_code == 401

    tomato, onion = (await api.get(OFFERS, headers=manager)).json()["items"]
    declined = {"outcome": "declined"}
    for who in (packer, owner, customer):
        assert (await settle(api, who, tomato["id"], declined)).status_code == 403
    assert code(await settle(api, elsewhere, tomato["id"], declined)) == (
        404,
        "hub_ops.offer_not_found",
    )
    assert (await settle(api, admin, tomato["id"], declined)).status_code == 200
    assert (await settle(api, manager, onion["id"], declined)).status_code == 200
