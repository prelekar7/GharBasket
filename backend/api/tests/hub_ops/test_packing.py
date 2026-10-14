"""Packing tasks (M6 §3.2, ADR-0026 §6): built once per locked box or add-on in delivery order, by
the hub manager or the 05:00 job; the queue, the summary and a task with its lines, for the
hub's own staff only."""

import uuid
from datetime import UTC, date, datetime, time
from typing import Any

import httpx2
import pytest
import time_machine
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.billing.wallet import credit_topup
from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.hub_ops import api as hub_ops_api
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.identity.models import User, UserRole
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.orders import service as order_service
from gharbasket.modules.orders.models import Order
from gharbasket.tools.seed import SEED_HUB_ID, SEED_SOCIETY_ID

from ..identity.conftest import ClientFactory, bearer, sign_in
from ..identity.fake_firebase import FakeGoogle
from .conftest import NEHA, PRIYA, SANJAY, VIKRAM, key

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("floor")]

NOW = datetime(2026, 10, 12, 23, 45, tzinfo=UTC)  # Tue 13 Oct, 05:15 IST
DELIVERY = date(2026, 10, 13)
SUNITA = "+919800001002"  # Green Valley Heights, B-504
RAMESH = "+919800001003"  # Krishna Kunj, A-101
MEENA = "+919800001004"  # Sunrise Towers, B-402 (Priya: Sunrise Towers, A-1203)
ARJUN = "+919800001006"  # a rider, given packer grants in these tests
TASKS = f"/v1/hubs/{SEED_HUB_ID}/packing-tasks"
# (steps, paid price per step). At the seed hub tomato (500 g, ₹25) has the substitutes
# bitter-gourd (250 g, ₹20), cucumber (500 g, ₹25) and brinjal (500 g, ₹30); potato is not one.
BOX = {"tomato": (2, 2_500), "bhindi": (2, 1_000), "onion": (2, 4_000)}


async def user_id(db: AsyncSession, phone: str) -> uuid.UUID:
    found = await db.scalar(select(User.id).where(User.phone_e164 == phone))
    assert found is not None
    return found


async def staff(
    api: httpx2.AsyncClient, google: FakeGoogle, phone: str
) -> tuple[dict[str, str], uuid.UUID]:
    signed = await sign_in(api, google, phone_number=phone, sub=f"uid{phone}")
    return bearer(signed["access_token"]), uuid.UUID(signed["user"]["id"])


async def grant(db: AsyncSession, phone: str, role: str, hub_id: uuid.UUID) -> None:
    db.add(UserRole(user_id=await user_id(db, phone), role=role, scope_type="hub", scope_id=hub_id))
    await db.commit()


async def locked_order(
    db: AsyncSession, phone: str, box: dict[str, tuple[int, int]] = BOX, start: time = time(7)
) -> tuple[uuid.UUID, dict[str, uuid.UUID]]:
    """A box paid from the owner's wallet and locked by the cut-off; committed."""
    owner = await user_id(db, phone)
    household = await identity_api.household_for_user(db, owner)
    assert household is not None
    slot = await order_service.create_slot(
        db,
        hub_id=SEED_HUB_ID,
        label=f"Packing {uuid.uuid4().hex[:8]}",
        kind="box",
        start=start,
        end=time(start.hour + 2),
        capacity=10,
    )
    ids = await catalog_api.product_ids_by_slug(db, list(box))
    order = await orders_api.create_from_cycle(
        db,
        orders_api.ApprovedCycle(
            cycle_id=uuid.uuid4(),
            household_id=household.id,
            hub_id=SEED_HUB_ID,
            society_id=household.society_id,
            delivery_date=DELIVERY,
            slot_id=slot.id,
            lines=tuple(orders_api.CycleLine(ids[s], q, p) for s, (q, p) in box.items()),
        ),
    )
    await credit_topup(
        db, owner, order.total_paise, payment_ref=uuid.uuid4().hex, topup_id=uuid.uuid4()
    )
    await billing_api.debit_for_order(db, owner, order.id, order.total_paise, DELIVERY)
    await orders_api.transition(db, order.id, "locked", actor="system")
    await db.commit()
    return order.id, ids


async def build(api: httpx2.AsyncClient, manager: dict[str, str]) -> dict[str, Any]:
    built = await api.post(
        f"{TASKS}/build", json={"date": DELIVERY.isoformat()}, headers={**manager, **key()}
    )
    assert built.status_code == 200, built.text
    return built.json()


async def queue(api: httpx2.AsyncClient, auth: dict[str, str], **params: Any) -> list[dict]:
    listed = await api.get(TASKS, params={"date": DELIVERY.isoformat(), **params}, headers=auth)
    assert listed.status_code == 200, listed.text
    return listed.json()["items"]


@time_machine.travel(NOW, tick=False)
async def test_building_makes_one_task_per_locked_box_in_delivery_order_once(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    manager, _ = await staff(api, google, VIKRAM)
    priya, _ = await locked_order(db_session, PRIYA)
    meena, _ = await locked_order(db_session, MEENA)
    sunita, _ = await locked_order(db_session, SUNITA)
    ramesh, _ = await locked_order(db_session, RAMESH, start=time(6))  # the earlier slot
    tiffin, _ = await locked_order(db_session, PRIYA)
    await db_session.execute(update(Order).where(Order.id == tiffin).values(kind="tiffin"))
    unpaid = await locked_order(db_session, MEENA)
    await db_session.execute(update(Order).where(Order.id == unpaid[0]).values(status="approved"))
    await db_session.commit()

    body, headers = {"date": DELIVERY.isoformat()}, {**manager, **key()}
    first = await api.post(f"{TASKS}/build", json=body, headers=headers)
    replay = await api.post(f"{TASKS}/build", json=body, headers=headers)

    assert first.json() == replay.json() == {"created": 4, "existing": 0}
    assert await build(api, manager) == {"created": 0, "existing": 4}
    tasks = await queue(api, manager)
    # Slot start, then society name, tower and flat; tiffins and unlocked orders are not packed.
    assert [(t["order_id"], t["seq"]) for t in tasks] == [
        (str(ramesh), 1),
        (str(sunita), 2),
        (str(priya), 3),
        (str(meena), 4),
    ]
    first_task = tasks[2]
    assert first_task["society"] == {"id": str(SEED_SOCIETY_ID), "name": "Sunrise Towers"}
    assert first_task["flat_label"] == "A wing, A-1203"
    assert first_task["bag_qr"] == f"GB1:{priya.hex}"
    assert first_task["slot"]["start"] == "07:00"
    assert (first_task["status"], first_task["packer"], first_task["route"]) == (
        "queued",
        None,
        None,
    )
    assert (first_task["lines_total"], first_task["lines_done"], first_task["bags"]) == (3, 0, 1)
    detail = (await api.get(f"/v1/packing-tasks/{first_task['id']}", headers=manager)).json()
    # Lines by catalog category (vegetables, then roots and tubers), then name; steps as ordered.
    assert [x["slug"] for x in detail["lines"]] == ["bhindi", "tomato", "onion"]
    assert detail["lines"][1]["step"] == {"qty": 500, "unit": "g", "label": "500 g"}
    assert (detail["lines"][1]["qty"], detail["lines"][1]["substitute"]) == (2, None)
    assert detail["version"] == 1

    # A late lock joins the end of the queue.
    late, _ = await locked_order(db_session, SUNITA, start=time(6))
    assert await build(api, manager) == {"created": 1, "existing": 4}
    page = (
        await api.get(TASKS, params={"date": DELIVERY.isoformat(), "limit": 3}, headers=manager)
    ).json()
    assert [t["seq"] for t in page["items"]] == [1, 2, 3]
    rest = await api.get(
        TASKS, params={"date": DELIVERY.isoformat(), "cursor": page["next_cursor"]}, headers=manager
    )
    assert [(t["seq"], t["order_id"]) for t in rest.json()["items"]] == [
        (4, str(meena)),
        (5, str(late)),
    ]
    assert rest.json()["next_cursor"] is None
    sunrise = await queue(api, manager, society_id=str(SEED_SOCIETY_ID))
    assert [t["order_id"] for t in sunrise] == [str(priya), str(meena)]

    summary = (await api.get(f"/v1/hubs/{SEED_HUB_ID}/packing-summary", headers=manager)).json()
    assert (summary["date"], summary["queued"], summary["picking"], summary["packed"]) == (
        DELIVERY.isoformat(),
        5,
        0,
        0,
    )
    assert [
        (s["name"], s["bags"], s["packed_bags"], s["crates"]) for s in summary["societies"]
    ] == [
        ("Green Valley Heights", 2, 0, 0),
        ("Krishna Kunj", 1, 0, 0),
        ("Sunrise Towers", 2, 0, 0),
    ]


@time_machine.travel(NOW, tick=False)
async def test_the_0500_job_builds_every_active_hub_for_its_scheduled_day(
    clients: ClientFactory, db_session: AsyncSession
) -> None:
    api = clients()
    built, _ = await locked_order(db_session, PRIYA)
    await locked_order(db_session, SUNITA)

    ran = await api.post(
        "/v1/internal/jobs/build_packing_tasks", json={"scheduled_for": "2026-10-12T23:30:00Z"}
    )
    again = await api.post(
        "/v1/internal/jobs/build_packing_tasks", json={"scheduled_for": "2026-10-12T23:40:00Z"}
    )

    assert ran.status_code == 200, ran.text
    assert ran.json()["summary"] == {
        "delivery_date": "2026-10-13",
        "hubs": 1,
        "created": 2,
        "existing": 0,
    }
    assert again.json()["summary"]["created"] == 0
    bag = await hub_ops_api.bag_for_order(db_session, (await locked_order(db_session, MEENA))[0])
    assert bag is None  # not built yet
    queued = await hub_ops_api.bag_for_order(db_session, built)
    assert queued == hub_ops_api.BagRef(f"GB1:{built.hex}", 1, None, "queued")
    loads = await hub_ops_api.society_loads(db_session, SEED_HUB_ID, DELIVERY)
    assert sorted((x.bags, x.packed_bags, x.crates) for x in loads) == [(1, 0, 0), (1, 0, 0)]


@time_machine.travel(NOW, tick=False)
async def test_hub_staff_see_only_their_hub(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    manager, _ = await staff(api, google, VIKRAM)
    await locked_order(db_session, PRIYA)
    await build(api, manager)
    task = (await queue(api, manager))[0]
    elsewhere = uuid.uuid4()
    await grant(db_session, ARJUN, "packer", elsewhere)
    outsider, _ = await staff(api, google, ARJUN)
    owner, _ = await staff(api, google, SANJAY)
    admin, _ = await staff(api, google, NEHA)
    customer, _ = await staff(api, google, PRIYA)
    await db_session.commit()

    assert (await api.get(TASKS, headers=outsider)).json()["code"] == "auth.forbidden"
    assert (await api.get(f"/v1/packing-tasks/{task['id']}", headers=outsider)).status_code == 404
    assert (await api.get(f"/v1/packing-tasks/{task['id']}", headers=customer)).status_code == 403
    # Arjun is a customer, the seed hub's rider and a packer elsewhere: no role he picks opens
    # the seed hub's packing (handbook §13.4).
    for active in ("customer", f"rider:{SEED_HUB_ID}", f"packer:{elsewhere}"):
        as_role = {**outsider, "X-Active-Role": active}
        assert (await api.get(TASKS, headers=as_role)).json()["code"] == "auth.forbidden"
        one = await api.get(f"/v1/packing-tasks/{task['id']}", headers=as_role)
        assert one.status_code == (404 if active.startswith("packer") else 403)
    # The franchise owner and admins read; only hub managers and admins build.
    assert (await api.get(f"/v1/packing-tasks/{task['id']}", headers=owner)).status_code == 200
    assert len(await queue(api, admin)) == 1
    body = {"date": DELIVERY.isoformat()}
    for who in (owner, outsider):
        refused = await api.post(f"{TASKS}/build", json=body, headers={**who, **key()})
        assert (refused.status_code, refused.json()["code"]) == (403, "auth.forbidden")
    assert (await api.post(f"{TASKS}/build", json=body, headers={**admin, **key()})).json() == {
        "created": 0,
        "existing": 1,
    }
    too_late = await api.get(TASKS, params={"date": "2026-10-16"}, headers=manager)
    assert (too_late.status_code, too_late.json()["code"]) == (422, "hub_ops.date_out_of_range")
    no_key = await api.post(f"{TASKS}/build", json=body, headers=manager)
    assert no_key.json()["code"] == "request.idempotency_key_required"


async def test_delivery_labels_give_tower_and_flat_only(db_session: AsyncSession) -> None:
    priya = await identity_api.household_for_user(db_session, await user_id(db_session, PRIYA))
    assert priya is not None

    labels = await identity_api.delivery_labels(db_session, [priya.id, uuid.uuid4()])

    assert labels == {priya.id: identity_api.DeliveryLabel("a", "A-1203")}
    assert await identity_api.delivery_labels(db_session, []) == {}
