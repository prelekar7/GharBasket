"""`GET /v1/analytics/hubs/{hub_id}/today` (M12 §3.3): the live board through the modules'
facades. A seeded morning at the seed hub gives every count, step and attention row; the date
limits, the hub scope and the absence of personal fields are checked on the answers."""

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import httpx2
import pytest
import time_machine
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog.models import Product
from gharbasket.modules.hub_ops.models import (
    PackingLine,
    PackingTask,
    ProcurementPlan,
    PurchaseOrder,
    Vendor,
    WasteLog,
)
from gharbasket.modules.logistics.models import Delivery, Route, RoutePlan, RouteStop, Vehicle
from gharbasket.modules.orders.models import Order, OrderIssue, Slot
from gharbasket.tools.seed import SEED_HUB_ID, SEED_SOCIETY_ID

from .. import seed_cache as cached
from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from ..subscriptions.test_cycles import as_user
from .test_kpis import PERSONAL, walk_keys

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.services,
    pytest.mark.usefixtures("delivery", "hubs"),
]

IST = ZoneInfo("Asia/Kolkata")
TUESDAY = date(2026, 11, 3)
SEVEN = datetime(2026, 11, 3, 7, 0, tzinfo=IST)  # after dispatch (06:30), before surplus (12:30)
GREEN_VALLEY = uuid.UUID("0199a000-0000-7000-8000-00000000b002")

NEHA = "+919800001011"  # admin
VIKRAM = "+919800001008"  # hub manager of the seed hub
SANJAY = "+919800001009"  # franchise owner of the seed hub
KAVITA = "+919800001007"  # packer at the seed hub
PRIYA = "+919800001001"  # customer


@pytest.fixture
async def hubs(db_session: AsyncSession) -> None:
    await cached.seed_hubs(db_session)


@pytest.fixture
def morning() -> Iterator[None]:
    with time_machine.travel(SEVEN, tick=False):
        yield


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), IST)


async def seed_morning(session: AsyncSession) -> dict[str, Any]:
    """The seed hub on Tuesday 3 Nov at 07:00: 4 boxes in two societies (one payment failed), 4
    of 5 bags packed, two routes (one started), one stop late, an open issue, a plan to review."""
    slot = (await session.execute(select(Slot).where(Slot.hub_id == SEED_HUB_ID))).scalars().one()
    product = (await session.execute(select(Product).order_by(Product.slug).limit(1))).scalar_one()

    def box(society: uuid.UUID, status: str) -> Order:
        return Order(
            household_id=uuid.uuid4(),
            hub_id=SEED_HUB_ID,
            society_id=society,
            kind="box",
            delivery_date=TUESDAY,
            slot_id=slot.id,
            cutoff_at=datetime(2026, 11, 2, 15, 30, tzinfo=UTC),
            status=status,
            subtotal_paise=60_000,
            total_paise=60_000,
            data_source="synthetic",
        )

    orders = [
        box(SEED_SOCIETY_ID, "packed"),
        box(SEED_SOCIETY_ID, "packed"),
        box(SEED_SOCIETY_ID, "dispatched"),
        box(GREEN_VALLEY, "payment_failed"),
        box(GREEN_VALLEY, "cancelled"),
    ]
    session.add_all(orders)
    await session.flush()
    session.add(
        OrderIssue(
            order_id=orders[0].id,
            household_id=orders[0].household_id,
            hub_id=SEED_HUB_ID,
            kind="missing",
            photo_keys=[],
            amount_paise=5_000,
            created_by=uuid.uuid4(),
            status="open",
            open_reason="triage",
            created_at=at(TUESDAY, 5, 30),
        )
    )

    def task(order: Order, status: str, bags: int = 1) -> PackingTask:
        return PackingTask(
            hub_id=SEED_HUB_ID,
            order_id=order.id,
            order_kind="box",
            delivery_date=TUESDAY,
            slot_id=slot.id,
            society_id=order.society_id,
            seq=1,
            status=status,
            bags=bags,
            packed_at=at(TUESDAY, 5, 45) if status == "packed" else None,
        )

    tasks = [task(orders[0], "packed", 2), task(orders[1], "packed"), task(orders[2], "packed")]
    tasks.append(task(orders[3], "queued"))
    session.add_all(tasks)
    await session.flush()
    session.add(
        PackingLine(
            task_id=tasks[0].id,
            product_id=product.id,
            step_qty=1,
            step_unit="kg",
            qty=2,
            picked_qty=1,
            short_qty=1,
        )
    )

    plan_ordered = ProcurementPlan(
        hub_id=SEED_HUB_ID,
        delivery_date=TUESDAY,
        status="ordered",
        source="fallback",
        buffer_pct=10,
        built_at=at(TUESDAY - timedelta(days=1), 21, 10),
    )
    vendors = [
        Vendor(hub_id=SEED_HUB_ID, name=f"Test vendor {i}", kind="wholesaler") for i in (1, 2)
    ]
    session.add_all([plan_ordered, *vendors])
    await session.flush()
    for vendor, status in zip(vendors, ("received", "sent"), strict=True):
        session.add(
            PurchaseOrder(
                plan_id=plan_ordered.id,
                hub_id=SEED_HUB_ID,
                vendor_id=vendor.id,
                delivery_date=TUESDAY,
                status=status,
                plan_version=1,
                total_paise=0,
                unpriced_lines=0,
                share_text="PO",
            )
        )

    van = Vehicle(
        hub_id=SEED_HUB_ID,
        label="EV-1",
        kind="cargo_3w",
        capacity_kg=500,
        capacity_crates=20,
        capacity_tiffins=0,
    )
    published = RoutePlan(
        hub_id=SEED_HUB_ID, delivery_date=TUESDAY, kind="box", status="published", trigger="job"
    )
    solved = RoutePlan(
        hub_id=SEED_HUB_ID,
        delivery_date=TUESDAY,
        kind="box",
        status="solved",
        trigger="manual",
        created_at=at(TUESDAY, 4, 15),
    )
    session.add_all([van, published, solved])
    await session.flush()

    def route(trip: int, status: str) -> Route:
        return Route(
            plan_id=published.id,
            hub_id=SEED_HUB_ID,
            delivery_date=TUESDAY,
            kind="box",
            trip=trip,
            vehicle_id=van.id,
            status=status,
            depart_time=time(6, 30),
            return_by=time(10),
            planned_km=9.0,
        )

    started, waiting = route(1, "in_progress"), route(2, "published")
    session.add_all([started, waiting])
    await session.flush()

    def stop(r: Route, status: str, end: time, handed: datetime | None = None) -> RouteStop:
        return RouteStop(
            route_id=r.id,
            seq=1 if status == "handed_over" else 2,
            society_id=SEED_SOCIETY_ID,
            window_start=time(6),
            window_end=end,
            eta=time(6, 50),
            status=status,
            handed_over_at=handed,
        )

    session.add_all(
        [
            stop(started, "handed_over", time(6, 45), at(TUESDAY, 6, 55)),  # late
            stop(started, "pending", time(9)),
            stop(waiting, "pending", time(9)),
        ]
    )
    for status in ("delivered", "with_captain", "held"):
        session.add(
            Delivery(
                order_id=uuid.uuid4(),
                attempt=1,
                hub_id=SEED_HUB_ID,
                society_id=SEED_SOCIETY_ID,
                order_kind="box",
                delivery_date=TUESDAY,
                slot_id=slot.id,
                status=status,
                delivery_code="1234",
            )
        )

    def waste(day: datetime, kg: str) -> WasteLog:
        return WasteLog(
            hub_id=SEED_HUB_ID,
            product_id=product.id,
            qty=Decimal(kg),
            unit="kg",
            kg=Decimal(kg),
            reason="spoiled",
            cascade_to="bin",
            at=day,
        )

    session.add_all(
        [
            waste(at(TUESDAY, 5, 0), "0.7"),
            waste(at(TUESDAY - timedelta(days=1), 11, 0), "1.0"),
            waste(at(TUESDAY - timedelta(days=2), 11, 0), "5.0"),  # older: not "the previous day"
        ]
    )
    await session.flush()
    return {"product": product, "solved": solved}


async def board(
    api: httpx2.AsyncClient, auth: dict[str, str], hub: uuid.UUID = SEED_HUB_ID, **params: Any
) -> httpx2.Response:
    return await api.get(f"/v1/analytics/hubs/{hub}/today", params=params, headers=auth)


async def signed_in(
    clients: ClientFactory, google: FakeGoogle, phone: str
) -> tuple[httpx2.AsyncClient, dict[str, str]]:
    api: httpx2.AsyncClient = clients()
    auth, _ = await as_user(api, google, phone)
    return api, auth


@pytest.mark.usefixtures("morning")
async def test_the_board_reads_every_module_live(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    seeded = await seed_morning(db_session)
    api, manager = await signed_in(clients, google, VIKRAM)

    answer = await board(api, manager)

    assert answer.status_code == 200, answer.text
    assert answer.headers["Cache-Control"] == "no-store"
    body = answer.json()
    assert (body["hub_id"], body["date"]) == (str(SEED_HUB_ID), "2026-11-03")
    assert datetime.fromisoformat(body["as_of"]) == SEVEN
    assert body["boxes"] == {
        "total": 4,  # the cancelled box is not one of the day
        "locked": 3,  # packed, packed, dispatched; the payment failed one is not
        "cutoff_at": "2026-11-02T15:30:00Z",
        "by_society": [
            {"society_id": str(GREEN_VALLEY), "name": "Green Valley Heights", "boxes": 1},
            {"society_id": str(SEED_SOCIETY_ID), "name": "Sunrise Towers", "boxes": 3},
        ],
    }
    assert body["packing"] == {"bags_total": 5, "bags_packed": 4, "finished_at": None}
    assert body["shortages"] == [
        {
            "product_id": str(seeded["product"].id),
            "name": seeded["product"].names["en"],
            "bags": 2,
        }
    ]
    assert body["routes"] == {
        "planned": 2,
        "started": 1,
        "completed": 0,
        "stops_total": 3,
        "stops_done": 1,
        "late_stops": 1,  # handed over at 06:55 for a window to 06:45; the others close at 09:00
    }
    assert body["deliveries"] == {"at_society": 1, "delivered": 1, "held": 1, "undelivered": 0}
    assert body["waste"] == {"kg": 0.7, "previous_kg": 1.0, "previous_date": "2026-11-02"}
    assert [(s["key"], s["due_at"], s["state"], s["note"]) for s in body["steps"]] == [
        ("cutoff", "21:00", "issue", "1 payment failed"),
        ("procurement", None, "done", None),
        ("receiving", "04:00", "issue", "1 of 2 orders received"),
        ("packing", "05:00", "issue", "4 of 5 bags"),
        ("dispatch", "06:30", "issue", "1 of 2 routes started"),
        ("delivery", None, "issue", "1 of 3 stops"),
        ("surplus", "12:30", "todo", None),
    ]
    assert body["attention"] == [
        {
            "kind": "issue_open",
            "count": 1,
            "oldest_at": "2026-11-03T00:00:00Z",
            "due_at": "2026-11-03T02:00:00Z",  # 120 minutes of callback promise
            "link": "/hub/issues?status=open",
        },
        {
            "kind": "payment_failed",
            "count": 1,
            "oldest_at": "2026-11-02T15:30:00Z",
            "due_at": "2026-11-02T17:30:00Z",  # the two-hour grace
            "link": "/hub/cutoff",
        },
        {
            "kind": "shortage",
            "count": 1,
            "oldest_at": None,
            "due_at": None,
            "link": "/hub/packing",
        },
        {
            "kind": "plan_needs_review",
            "count": 1,
            "oldest_at": seeded["solved"]
            .created_at.astimezone(UTC)
            .isoformat()
            .replace("+00:00", "Z"),
            "due_at": None,
            "link": "/hub/routes",
        },
        {
            "kind": "delivery_held",
            "count": 1,
            "oldest_at": None,
            "due_at": None,
            "link": "/hub/deliveries",
        },
    ]
    kinds = {row["kind"] for row in body["attention"]}
    assert not kinds & {"donations_to_verify", "surplus_open", "society_live"}  # no source yet
    assert not PERSONAL & set(walk_keys(body))
    assert all(
        row["link"].startswith("/") and "token" not in row["link"] for row in body["attention"]
    )


@time_machine.travel(datetime(2026, 11, 3, 4, 30, tzinfo=IST), tick=False)
async def test_an_early_morning_and_a_quiet_day_have_no_attention_and_todo_steps(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    slot = (
        (await db_session.execute(select(Slot).where(Slot.hub_id == SEED_HUB_ID))).scalars().one()
    )
    plan = ProcurementPlan(
        hub_id=SEED_HUB_ID,
        delivery_date=TUESDAY,
        status="ordered",
        source="fallback",
        buffer_pct=10,
        built_at=at(TUESDAY - timedelta(days=1), 21, 10),
    )
    vendor = Vendor(hub_id=SEED_HUB_ID, name="Test vendor", kind="wholesaler")
    db_session.add_all([plan, vendor])
    await db_session.flush()
    db_session.add(
        PurchaseOrder(
            plan_id=plan.id,
            hub_id=SEED_HUB_ID,
            vendor_id=vendor.id,
            delivery_date=TUESDAY,
            status="sent",
            plan_version=1,
            total_paise=0,
            unpriced_lines=0,
            share_text="PO",
        )
    )
    assert slot is not None
    api, owner = await signed_in(clients, google, SANJAY)

    answer = await board(api, owner)

    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert [(s["key"], s["state"]) for s in body["steps"]] == [
        ("cutoff", "done"),  # last night's 21:00, no failed payment
        ("procurement", "done"),  # the plan is ordered
        ("receiving", "current"),  # 04:00 has begun, nothing received, 05:00 not yet
        ("packing", "todo"),
        ("dispatch", "todo"),
        ("delivery", "todo"),
        ("surplus", "todo"),
    ]
    assert body["attention"] == [] and body["shortages"] == []
    assert body["boxes"]["by_society"] == [] and body["waste"] == {
        "kg": 0.0,
        "previous_kg": None,
        "previous_date": None,
    }

    tomorrow = (await board(api, owner, date="2026-11-04")).json()
    cutoff = tomorrow["steps"][0]
    # Tomorrow's cut-off is tonight at 21:00: the day before it, the step is the current one
    assert (cutoff["key"], cutoff["state"], cutoff["due_at"]) == ("cutoff", "current", "21:00")
    assert tomorrow["boxes"]["cutoff_at"] == "2026-11-03T15:30:00Z"
    assert {s["state"] for s in tomorrow["steps"][1:]} == {"todo"}


@pytest.mark.usefixtures("morning")
async def test_the_date_is_limited_to_a_week_back_and_tomorrow(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api, manager = await signed_in(clients, google, VIKRAM)

    for day, status in [
        ("2026-10-27", 200),  # today - 7
        ("2026-10-26", 422),
        ("2026-11-04", 200),  # today + 1
        ("2026-11-05", 422),
        ("1999-01-01", 422),
    ]:
        answer = await board(api, manager, date=day)
        assert answer.status_code == status, (day, answer.text)
    refused = await board(api, manager, date="2026-11-05")
    assert refused.json()["code"] == "analytics.range_invalid"
    assert refused.json().get("errors") is None  # our code, not the framework's field error
    default = await board(api, manager)
    assert default.json()["date"] == "2026-11-03"
    assert (await board(api, manager, date="soon")).status_code == 422


@pytest.mark.usefixtures("morning")
async def test_hub_scope_and_roles(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api, manager = await signed_in(clients, google, VIKRAM)

    elsewhere = await board(api, manager, uuid.uuid4())
    assert elsewhere.status_code == 403 and elsewhere.json()["code"] == "auth.forbidden"
    chosen = await board(api, {**manager, "X-Active-Role": f"hub_manager:{SEED_HUB_ID}"})
    assert chosen.status_code == 200, chosen.text
    other_role = await board(api, {**manager, "X-Active-Role": "customer"})
    assert other_role.status_code == 403  # as a customer she may not read the board
    for phone in (PRIYA, KAVITA):  # a customer, a packer
        other_api, auth = await signed_in(clients, google, phone)
        assert (await board(other_api, auth)).status_code == 403
    assert (await api.get(f"/v1/analytics/hubs/{SEED_HUB_ID}/today")).status_code == 401

    admin_api, admin = await signed_in(clients, google, NEHA)
    assert (await board(admin_api, admin)).status_code == 200
    unknown = await board(admin_api, admin, uuid.uuid4())
    assert unknown.status_code == 404 and unknown.json()["code"] == "hub_ops.hub_not_found"
    owner_api, owner = await signed_in(clients, google, SANJAY)
    assert (await board(owner_api, owner)).status_code == 200
