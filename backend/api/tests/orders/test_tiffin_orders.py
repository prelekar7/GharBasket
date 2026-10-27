"""Direct tiffin orders (M4 §3.5): one per household, date and meal, in the hub's tiffin slot for
the meal, the menu's kitchen as the preferred cook, paid at the cut-off like a box."""

import uuid
from datetime import UTC, date, datetime, time
from typing import Any

import httpx2
import pytest
import time_machine
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing.wallet import credit_topup
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.orders import service
from gharbasket.modules.orders.models import Order, SlotBooking
from gharbasket.platform.models import OutboxEvent
from gharbasket.tools.seed import SEED_HUB_ID

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .test_orders import PRIYA, SUNITA, VIKRAM, as_user
from .tiffin_menus import menu

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("world")]

NOW = datetime(2026, 10, 10, 10, 0, tzinfo=UTC)  # Sat 15:30 IST
DAY = date(2026, 10, 12)  # Monday; cut-off Sun 21:00 IST


def key() -> dict[str, str]:
    return {"Idempotency-Key": uuid.uuid4().hex}


def code(answer: httpx2.Response) -> tuple[int, str]:
    return answer.status_code, answer.json().get("code", "")


async def tiffin_slot(db: AsyncSession, meal: str = "lunch", capacity: int = 10) -> uuid.UUID:
    made = await service.create_slot(
        db,
        hub_id=SEED_HUB_ID,
        label=f"Tiffin {meal} {uuid.uuid4().hex[:4]}",
        kind="tiffin",
        start=time(12, 30) if meal == "lunch" else time(19, 30),
        end=time(13, 30) if meal == "lunch" else time(20, 30),
        capacity=capacity,
        meal=meal,
    )
    return made.id


async def book(api: httpx2.AsyncClient, auth: dict[str, str], **body: Any) -> httpx2.Response:
    return await api.post("/v1/orders", json={"kind": "tiffin", **body}, headers={**auth, **key()})


@time_machine.travel(NOW, tick=False)
async def test_a_household_books_one_tiffin_per_meal_from_a_menu(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    slot_id = await tiffin_slot(db_session)
    cook = uuid.uuid4()
    lunch = (await menu(db_session, DAY, partner_id=cook, price_paise=11_000)).id
    await db_session.commit()
    api = clients()
    priya, priya_id = await as_user(api, google, PRIYA)
    household = await identity_api.household_for_user(db_session, priya_id)
    assert household is not None
    once = {**priya, **key()}

    made = await api.post(
        "/v1/orders", json={"kind": "tiffin", "menu_id": str(lunch), "portions": 2}, headers=once
    )
    replay = await api.post(
        "/v1/orders", json={"kind": "tiffin", "menu_id": str(lunch), "portions": 2}, headers=once
    )
    again = await book(api, priya, menu_id=str(lunch), portions=1)

    assert made.status_code == 201, made.text
    body = made.json()
    assert (body["kind"], body["status"], body["delivery_date"]) == (
        "tiffin",
        "approved",
        "2026-10-12",
    )
    assert body["slot"]["id"] == str(slot_id) and body["cutoff_at"] == "2026-10-11T15:30:00Z"
    assert [(ln["qty"], ln["unit_price_paise"], ln["total_paise"]) for ln in body["lines"]] == [
        (2, 11_000, 22_000)
    ]
    assert (body["total_paise"], body["fees_paise"]) == (22_000, 0)
    assert body["tiffin"]["menu_id"] == str(lunch) and body["tiffin"]["meal"] == "lunch"
    assert len(body["tiffin"]["dishes"]) == 2
    assert replay.json()["id"] == body["id"]
    assert code(again) == (409, "orders.tiffin_already_booked")
    assert again.json()["order_id"] == body["id"]
    order = await db_session.get(Order, uuid.UUID(body["id"]))
    assert order is not None
    assert (order.preferred_partner_id, order.portions, order.tiffin_menu_id) == (cook, 2, lunch)
    booked = await db_session.execute(
        select(OutboxEvent.payload).where(OutboxEvent.event_type == "orders.tiffin_booked")
    )
    assert booked.scalars().all() == [
        {
            "order_id": body["id"],
            "household_id": str(household.id),
            "delivery_date": "2026-10-12",
            "meal": "lunch",
            "portions": 2,
        }
    ]
    assert (
        await db_session.scalar(select(SlotBooking.slot_id).where(SlotBooking.order_id == order.id))
        == slot_id
    )

    # Cancelled for free before the cut-off, the meal can be booked again (default portions:
    # the household's size).
    cancelled = await api.post(f"/v1/orders/{body['id']}/cancel", headers={**priya, **key()})
    rebooked = await book(api, priya, menu_id=str(lunch))
    assert cancelled.json()["status"] == "cancelled"
    assert rebooked.status_code == 201, rebooked.text
    profile = await identity_api.household_plan_profile(db_session, household.id)
    assert profile is not None and rebooked.json()["lines"][0]["qty"] == profile.size

    # Paid at the cut-off like a box.
    await credit_topup(
        db_session, priya_id, 100_000, payment_ref=uuid.uuid4().hex, topup_id=uuid.uuid4()
    )
    await db_session.commit()
    locked = await api.post(
        "/v1/internal/jobs/cutoff_lock", json={"scheduled_for": "2026-10-11T15:30:00Z"}
    )
    assert locked.json()["status"] == "ok", locked.text
    paid = await db_session.get(Order, uuid.UUID(rebooked.json()["id"]))
    assert paid is not None
    await db_session.refresh(paid)
    assert paid.status == "locked"


@time_machine.travel(NOW, tick=False)
async def test_what_cannot_be_booked(
    clients: ClientFactory,
    google: FakeGoogle,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await tiffin_slot(db_session, capacity=1)
    lunch = (await menu(db_session, DAY)).id
    unreviewed = (await menu(db_session, DAY, meal="dinner", status="submitted")).id
    elsewhere = (await menu(db_session, DAY, hub_id=uuid.uuid4())).id
    dinner = (await menu(db_session, DAY, meal="dinner")).id  # the hub has no dinner slot
    await db_session.commit()
    api = clients()
    priya, _ = await as_user(api, google, PRIYA)
    sunita, _ = await as_user(api, google, SUNITA)
    vikram, _ = await as_user(api, google, VIKRAM)

    addon = await api.post(
        "/v1/orders", json={"kind": "addon", "menu_id": str(lunch)}, headers={**priya, **key()}
    )
    no_menu = await book(api, priya)
    unknown = await book(api, priya, menu_id=str(uuid.uuid4()))
    unapproved = await book(api, priya, menu_id=str(unreviewed))
    other_hub = await book(api, priya, menu_id=str(elsewhere))
    too_many = await book(api, priya, menu_id=str(lunch), portions=13)
    none = await book(api, priya, menu_id=str(lunch), portions=0)
    no_slot = await book(api, priya, menu_id=str(dinner))
    staff = await book(api, vikram, menu_id=str(lunch))
    first = await book(api, priya, menu_id=str(lunch), portions=1)
    full = await book(api, sunita, menu_id=str(lunch), portions=1)
    monkeypatch.setattr(service, "now", lambda: datetime(2026, 10, 11, 15, 30, tzinfo=UTC))
    late = await book(api, sunita, menu_id=str(dinner))

    assert code(addon) == (422, "orders.kind_unavailable")
    for answer in (no_menu, unknown, unapproved, other_hub, late):
        assert code(answer) == (422, "orders.menu_unknown"), answer.text
    assert code(too_many) == code(none) == (422, "orders.qty_invalid")
    assert code(no_slot) == (422, "orders.slot_invalid")
    assert staff.status_code in (403, 422)
    assert first.status_code == 201, first.text
    assert code(full) == (409, "orders.slot_full")
    assert (await db_session.execute(select(Order).where(Order.kind == "tiffin"))).all()


@time_machine.travel(NOW, tick=False)
async def test_a_tiffin_slot_names_its_meal_and_a_box_slot_has_none(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    vikram, _ = await as_user(api, google, VIKRAM)
    body = {"hub_id": str(SEED_HUB_ID), "start": "12:30", "end": "13:30", "capacity": 20}

    made = await api.post(
        "/v1/admin/slots",
        json={**body, "label": "Lunch tiffins", "kind": "tiffin", "meal": "lunch"},
        headers={**vikram, **key()},
    )
    no_meal = await api.post(
        "/v1/admin/slots",
        json={**body, "label": "No meal", "kind": "tiffin"},
        headers={**vikram, **key()},
    )
    box_meal = await api.post(
        "/v1/admin/slots",
        json={**body, "label": "Box meal", "kind": "box", "meal": "dinner"},
        headers={**vikram, **key()},
    )

    assert made.status_code == 201, made.text
    assert (made.json()["kind"], made.json()["meal"]) == ("tiffin", "lunch")
    assert code(no_meal) == code(box_meal) == (422, "orders.slot_invalid")
