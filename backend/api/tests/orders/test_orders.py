"""Slots, orders from approved cycles, add-ons, cancel and the facade (M4 §3.1)."""

import uuid
from datetime import UTC, date, datetime, time, timedelta

import httpx2
import pytest
import time_machine
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.orders import service
from gharbasket.modules.orders.models import OrderEvent, SlotBooking
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.models import OutboxEvent
from gharbasket.tools.seed import SEED_HUB_ID

from ..identity.conftest import ClientFactory, bearer, sign_in
from ..identity.fake_firebase import FakeGoogle

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("world")]

PRIYA = "+919800001001"  # customer, household at Sunrise Towers (seed hub)
SUNITA = "+919800001002"  # another customer
VIKRAM = "+919800001008"  # hub manager of the seed hub
NOW = datetime(2026, 10, 10, 10, 0, tzinfo=UTC)  # Sat 15:30 IST
DELIVERY = date(2026, 10, 13)  # Tuesday; cut-off Mon 21:00 IST = 15:30 UTC


async def as_user(
    api: httpx2.AsyncClient, google: FakeGoogle, phone: str
) -> tuple[dict, uuid.UUID]:
    signed = await sign_in(api, google, phone_number=phone, sub=f"uid{phone}")
    return bearer(signed["access_token"]), uuid.UUID(signed["user"]["id"])


async def make_slot(
    api: httpx2.AsyncClient, auth: dict, label: str = "Test 7-9", capacity: int = 2
) -> dict:
    answer = await api.post(
        "/v1/admin/slots",
        json={
            "hub_id": str(SEED_HUB_ID),
            "label": label,
            "start": "07:00",
            "end": "09:00",
            "capacity": capacity,
        },
        headers={**auth, "Idempotency-Key": f"slot-{label}-{uuid.uuid4()}"},
    )
    assert answer.status_code == 201, answer.text
    return answer.json()


async def approved_cycle(
    db: AsyncSession, household: identity_api.HouseholdRef, slot_id: uuid.UUID, *slugs: str
) -> orders_api.ApprovedCycle:
    ids = await catalog_api.product_ids_by_slug(db, list(slugs))
    products = await catalog_api.line_products(db, list(ids.values()), hub_id=SEED_HUB_ID)
    return orders_api.ApprovedCycle(
        cycle_id=uuid.uuid4(),
        household_id=household.id,
        hub_id=SEED_HUB_ID,
        society_id=household.society_id,
        delivery_date=DELIVERY,
        slot_id=slot_id,
        lines=tuple(
            orders_api.CycleLine(
                ids[s], 2, products[ids[s]].price_paise or 0, "in_season" if i == 0 else None
            )
            for i, s in enumerate(slugs)
        ),
        data_source="synthetic",
    )


@time_machine.travel(NOW, tick=False)
async def test_slots_show_availability_and_only_hub_staff_define_them(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api = clients()
    priya, _ = await as_user(api, google, PRIYA)
    vikram, _ = await as_user(api, google, VIKRAM)

    refused = await api.post(
        "/v1/admin/slots",
        json={
            "hub_id": str(SEED_HUB_ID),
            "label": "x",
            "start": "07:00",
            "end": "09:00",
            "capacity": 5,
        },
        headers={**priya, "Idempotency-Key": "s1"},
    )
    slot = await make_slot(api, vikram)
    twice = await api.post(
        "/v1/admin/slots",
        json={
            "hub_id": str(SEED_HUB_ID),
            "label": "Test 7-9",
            "start": "07:00",
            "end": "09:00",
            "capacity": 5,
        },
        headers={**vikram, "Idempotency-Key": "s2"},
    )
    backwards = await api.post(
        "/v1/admin/slots",
        json={
            "hub_id": str(SEED_HUB_ID),
            "label": "Odd",
            "start": "09:00",
            "end": "07:00",
            "capacity": 5,
        },
        headers={**vikram, "Idempotency-Key": "s3"},
    )
    other_hub = await api.post(
        "/v1/admin/slots",
        json={
            "hub_id": str(uuid.uuid4()),
            "label": "Elsewhere",
            "start": "07:00",
            "end": "09:00",
            "capacity": 5,
        },
        headers={**vikram, "Idempotency-Key": "s4"},
    )

    assert refused.status_code == 403
    assert (twice.status_code, twice.json()["code"]) == (409, "orders.slot_exists")
    assert (backwards.status_code, backwards.json()["code"]) == (422, "orders.slot_invalid")
    assert other_hub.status_code == 403
    listed = await api.get("/v1/slots", headers=priya)  # Priya's hub, tomorrow
    assert listed.json()["date"] == "2026-10-11"
    ours = [s for s in listed.json()["items"] if s["id"] == slot["id"]]
    assert ours == [{**slot, "booked": 0, "available": 2}]
    too_far = await api.get("/v1/slots?date=2026-11-30", headers=priya)
    assert too_far.json()["code"] == "orders.date_out_of_range"
    closed = await api.patch(
        f"/v1/admin/slots/{slot['id']}", json={"active": False, "capacity": 3}, headers=vikram
    )
    assert closed.json()["capacity"] == 3
    after = (await api.get("/v1/slots", headers=priya)).json()["items"]
    assert not any(s["id"] == slot["id"] for s in after)


@time_machine.travel(NOW, tick=False)
async def test_an_approved_cycle_becomes_an_order_once_with_frozen_prices_and_a_booking(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, priya_id = await as_user(api, google, PRIYA)
    vikram, _ = await as_user(api, google, VIKRAM)
    slot = await make_slot(api, vikram, capacity=1)
    household = await identity_api.household_for_user(db_session, priya_id)
    assert household is not None
    cycle = await approved_cycle(db_session, household, uuid.UUID(slot["id"]), "tomato", "bhindi")

    order = await orders_api.create_from_cycle(db_session, cycle)
    order_id = order.id
    again = await orders_api.create_from_cycle(db_session, cycle)

    assert again.id == order.id
    assert order.status == "approved" and order.cutoff_at == datetime(
        2026, 10, 12, 15, 30, tzinfo=UTC
    )
    assert order.total_paise == sum(line.qty * line.unit_price_paise for line in cycle.lines) > 0
    booked = (await db_session.execute(select(SlotBooking))).scalars().all()
    assert len(booked) == 1 and booked[0].on_date == DELIVERY
    # The slot is full now: another household's cycle cannot book it.
    sunita_household = await identity_api.household_for_user(
        db_session, (await as_user(api, google, SUNITA))[1]
    )
    assert sunita_household is not None
    other = await approved_cycle(db_session, sunita_household, uuid.UUID(slot["id"]), "onion")
    with pytest.raises(ProblemError) as full:
        await orders_api.create_from_cycle(db_session, other)
    assert full.value.code == "orders.slot_full"
    await db_session.commit()  # a savepoint: failing requests below must not undo the order

    detail = await api.get(f"/v1/orders/{order_id}", headers=priya)
    body = detail.json()
    assert detail.headers["etag"] == '"1"' and detail.headers["cache-control"] == "no-store"
    assert sorted(
        (line["slug"], line["kind"], line["qty"], line["reason_code"]) for line in body["lines"]
    ) == [
        ("bhindi", "box", 2, None),
        ("tomato", "box", 2, "in_season"),
    ]
    assert (
        body["lines"][1]["step"]["label"]
        and body["lines"][1]["total_paise"] == 2 * body["lines"][1]["unit_price_paise"]
    )
    assert body["slot"]["label"] == "Test 7-9" and body["timeline"] == [
        {"status": "approved", "at": body["timeline"][0]["at"], "actor": "customer"}
    ]
    listed = await api.get("/v1/orders", headers=priya)
    assert listed.status_code == 200, listed.text
    assert [o["id"] for o in listed.json()["items"]] == [str(order_id)]
    assert "lines" not in listed.json()["items"][0]
    # Another customer sees nothing; the hub manager sees it under the hub.
    sunita, _ = await as_user(api, google, SUNITA)
    assert (await api.get(f"/v1/orders/{order_id}", headers=sunita)).status_code == 404
    sunita_list = await api.get("/v1/orders", headers=sunita)
    assert sunita_list.status_code == 200, sunita_list.text
    assert sunita_list.json()["items"] == []
    staff = await api.get(
        f"/v1/orders?hub_id={SEED_HUB_ID}",
        headers={**vikram, "X-Active-Role": f"hub_manager:{SEED_HUB_ID}"},
    )
    assert staff.status_code == 200, staff.text
    assert [o["id"] for o in staff.json()["items"]] == [str(order_id)]
    needs_hub = await api.get(
        "/v1/orders", headers={**vikram, "X-Active-Role": f"hub_manager:{SEED_HUB_ID}"}
    )
    assert needs_hub.json()["code"] == "orders.hub_required"


@time_machine.travel(NOW, tick=False)
async def test_addons_are_priced_now_versioned_and_refused_after_the_cutoff(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, priya_id = await as_user(api, google, PRIYA)
    vikram, _ = await as_user(api, google, VIKRAM)
    slot = await make_slot(api, vikram)
    household = await identity_api.household_for_user(db_session, priya_id)
    assert household is not None
    order = await orders_api.create_from_cycle(
        db_session, await approved_cycle(db_session, household, uuid.UUID(slot["id"]), "tomato")
    )
    order_id = order.id
    ids = await catalog_api.product_ids_by_slug(db_session, ["paneer", "curd", "tomato"])
    await db_session.commit()  # a savepoint: failing requests below must not undo the order
    url = f"/v1/orders/{order_id}/addons"

    no_match = await api.post(
        url,
        json={"items": [{"product_id": str(ids["paneer"]), "qty": 1}]},
        headers={**priya, "Idempotency-Key": "a0"},
    )
    stale = await api.post(
        url,
        json={"items": [{"product_id": str(ids["paneer"]), "qty": 1}]},
        headers={**priya, "Idempotency-Key": "a1", "If-Match": '"7"'},
    )
    added = await api.post(
        url,
        json={
            "items": [
                {"product_id": str(ids["paneer"]), "qty": 2},
                {"product_id": str(ids["curd"]), "qty": 1},
            ]
        },
        headers={**priya, "Idempotency-Key": "a2", "If-Match": '"1"'},
    )

    assert no_match.status_code == 428
    assert (stale.status_code, stale.json()["code"]) == (412, "request.version_mismatch")
    assert added.status_code == 200 and added.headers["etag"] == '"2"'
    body = added.json()
    addons = {line["slug"]: line for line in body["lines"] if line["kind"] == "addon"}
    assert set(addons) == {"paneer", "curd"} and addons["paneer"]["qty"] == 2
    assert body["total_paise"] == sum(line["total_paise"] for line in body["lines"])
    replay = await api.post(
        url,
        json={
            "items": [
                {"product_id": str(ids["paneer"]), "qty": 2},
                {"product_id": str(ids["curd"]), "qty": 1},
            ]
        },
        headers={**priya, "Idempotency-Key": "a2", "If-Match": '"1"'},
    )
    assert replay.json() == body and replay.headers["idempotent-replayed"] == "true"

    removed = await api.post(
        url,
        json={"items": [{"product_id": str(ids["curd"]), "qty": 0}]},
        headers={**priya, "Idempotency-Key": "a3", "If-Match": '"2"'},
    )
    assert [line["slug"] for line in removed.json()["lines"] if line["kind"] == "addon"] == [
        "paneer"
    ]
    box_line = await api.post(
        url,
        json={"items": [{"product_id": str(ids["tomato"]), "qty": 3}]},
        headers={**priya, "Idempotency-Key": "a4", "If-Match": '"3"'},
    )
    assert box_line.json()["code"] == "orders.qty_invalid"
    unknown = await api.post(
        url,
        json={"items": [{"product_id": str(uuid.uuid4()), "qty": 1}]},
        headers={**priya, "Idempotency-Key": "a5", "If-Match": '"3"'},
    )
    assert unknown.json()["code"] == "orders.product_unknown"

    with time_machine.travel(datetime(2026, 10, 12, 15, 31, tzinfo=UTC), tick=False):  # 21:01 IST
        priya, _ = await as_user(api, google, PRIYA)  # the earlier token has expired by now
        late = await api.post(
            url,
            json={"items": [{"product_id": str(ids["paneer"]), "qty": 1}]},
            headers={**priya, "Idempotency-Key": "a6", "If-Match": '"3"'},
        )
        cancel_late = await api.post(
            f"/v1/orders/{order_id}/cancel", headers={**priya, "Idempotency-Key": "c1"}
        )
    assert late.json()["code"] == "orders.cutoff_passed"
    assert cancel_late.json()["code"] == "orders.cutoff_passed"


@time_machine.travel(NOW, tick=False)
async def test_cancel_frees_the_slot_and_locked_orders_cannot_be_cancelled(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, priya_id = await as_user(api, google, PRIYA)
    vikram, _ = await as_user(api, google, VIKRAM)
    slot = await make_slot(api, vikram, capacity=1)
    household = await identity_api.household_for_user(db_session, priya_id)
    assert household is not None
    order = await orders_api.create_from_cycle(
        db_session, await approved_cycle(db_session, household, uuid.UUID(slot["id"]), "tomato")
    )
    order_id = order.id

    await db_session.commit()
    cancelled = await api.post(
        f"/v1/orders/{order_id}/cancel", headers={**priya, "Idempotency-Key": "c1"}
    )
    replay = await api.post(
        f"/v1/orders/{order_id}/cancel", headers={**priya, "Idempotency-Key": "c1"}
    )

    assert cancelled.json()["status"] == "cancelled" and replay.json() == cancelled.json()
    assert [e["status"] for e in cancelled.json()["timeline"]] == ["approved", "cancelled"]
    # The timeline names the kind of actor only; the row remembers who it was.
    assert cancelled.json()["timeline"][-1] == {
        "status": "cancelled",
        "at": cancelled.json()["timeline"][-1]["at"],
        "actor": "customer",
    }
    who = await db_session.execute(
        select(OrderEvent.actor, OrderEvent.actor_user_id).where(
            OrderEvent.order_id == order_id, OrderEvent.to_status == "cancelled"
        )
    )
    assert who.one() == ("customer", priya_id)
    assert (await db_session.execute(select(SlotBooking))).scalars().all() == []
    events = (await db_session.execute(select(OutboxEvent.event_type, OutboxEvent.payload))).all()
    assert ("orders.cancelled", {"reason": "customer"}) in [
        (t, {k: v for k, v in p.items() if k == "reason"}) for t, p in events
    ]

    # A second order, locked by the system: the customer cannot cancel it any more.
    second = await orders_api.create_from_cycle(
        db_session, await approved_cycle(db_session, household, uuid.UUID(slot["id"]), "onion")
    )
    second_id = second.id
    await orders_api.transition(db_session, second_id, "locked", actor="system")
    await db_session.commit()
    locked = await api.post(
        f"/v1/orders/{second_id}/cancel", headers={**priya, "Idempotency-Key": "c2"}
    )
    assert (locked.status_code, locked.json()["code"]) == (409, "orders.already_locked")
    detail = (await api.get(f"/v1/orders/{second_id}", headers=priya)).json()
    assert detail["status"] == "locked" and detail["locked_at"] is not None
    published = (
        await db_session.execute(select(OutboxEvent.event_type, OutboxEvent.payload))
    ).all()
    locked_event = next(p for t, p in published if t == "orders.locked")
    assert locked_event["order_id"] == str(second_id) and locked_event["lines"][0]["qty"] == 2


@time_machine.travel(NOW, tick=False)
async def test_the_facade_serves_fulfilment_and_shortages(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    _, priya_id = await as_user(api, google, PRIYA)
    vikram, _ = await as_user(api, google, VIKRAM)
    slot = await make_slot(api, vikram)
    household = await identity_api.household_for_user(db_session, priya_id)
    assert household is not None
    cycle = await approved_cycle(db_session, household, uuid.UUID(slot["id"]), "tomato", "onion")
    order = await orders_api.create_from_cycle(db_session, cycle)
    order_id = order.id
    ids = {line.product_id for line in cycle.lines}

    assert await orders_api.orders_for_delivery(db_session, SEED_HUB_ID, DELIVERY) == []
    await orders_api.transition(db_session, order_id, "locked", actor="system")
    today = await orders_api.orders_for_delivery(db_session, SEED_HUB_ID, DELIVERY)
    assert [s.order.id for s in today] == [order_id] and {
        line.product_id for line in today[0].lines
    } == ids

    tomato_id = (await catalog_api.product_ids_by_slug(db_session, ["tomato"]))["tomato"]
    line = await orders_api.mark_shortage(db_session, order_id, tomato_id, 1, None)
    assert line.shortage_qty == 1
    with pytest.raises(ProblemError) as too_many:
        await orders_api.mark_shortage(db_session, order_id, tomato_id, 5, None)
    assert too_many.value.code == "orders.qty_invalid"
    with pytest.raises(ProblemError) as illegal:
        await orders_api.transition(db_session, order_id, "delivered", actor="hub")
    assert illegal.value.code == "orders.illegal_transition"
    packer, rider, captain = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    with pytest.raises(ProblemError) as not_a_kitchen_job:
        await orders_api.transition(
            db_session, order_id, "picking", actor="kitchen", actor_user_id=uuid.uuid4()
        )
    assert not_a_kitchen_job.value.extra == {
        "kind": "box",
        "from": "locked",
        "to": "picking",
        "actor": "kitchen",
    }
    moves = [
        ("picking", "packer", packer),
        ("packed", "packer", packer),
        ("dispatched", "rider", rider),
        ("at_society", "captain", captain),
        ("delivered", "captain", captain),
    ]
    for to, actor, user_id in moves:
        await orders_api.transition(
            db_session,
            order_id,
            to,
            actor=actor,
            actor_user_id=user_id,
            payload={"scan": "GB1"} if to == "at_society" else None,
        )
    published = (
        await db_session.execute(
            select(OutboxEvent.event_type, OutboxEvent.payload).order_by(OutboxEvent.id)
        )
    ).all()
    assert [(t, p["kind"], p["actor"]) for t, p in published[-4:]] == [
        ("orders.packed", "box", "packer"),
        ("orders.dispatched", "box", "rider"),
        ("orders.at_society", "box", "captain"),
        ("orders.delivered", "box", "captain"),
    ]
    handover = published[-2].payload
    assert handover["scan"] == "GB1" and handover["order_id"] == str(order_id)
    assert handover["society_id"] == str(household.society_id) and "actor_user_id" not in handover
    timeline = (
        await db_session.execute(
            select(OrderEvent.to_status, OrderEvent.actor, OrderEvent.actor_user_id)
            .where(OrderEvent.order_id == order_id)
            .order_by(OrderEvent.id)
        )
    ).all()
    assert timeline[0] == ("approved", "customer", None)
    assert timeline[1] == ("locked", "system", None)
    assert [tuple(row) for row in timeline[2:]] == moves
    assert await orders_api.cancel_from_cycle(db_session, uuid.uuid4()) is None
    skipped_order = await orders_api.create_from_cycle(
        db_session, await approved_cycle(db_session, household, uuid.UUID(slot["id"]), "bhindi")
    )
    assert skipped_order.cycle_id is not None
    cancelled = await orders_api.cancel_from_cycle(db_session, skipped_order.cycle_id)
    assert cancelled is not None and cancelled.status == "cancelled"
    assert service.cutoff_for(date(2026, 10, 13)) == datetime(2026, 10, 12, 15, 30, tzinfo=UTC)
    assert service.cutoff_for(date(2026, 10, 13)).astimezone(service.IST).time() == time(21, 0)
    assert DELIVERY - timedelta(days=1) == date(2026, 10, 12)


@time_machine.travel(NOW, tick=False)
async def test_a_tiffin_is_cooked_picked_up_and_handed_over_without_the_society_hold(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    priya, priya_id = await as_user(api, google, PRIYA)
    vikram, _ = await as_user(api, google, VIKRAM)
    slot_id = uuid.UUID((await make_slot(api, vikram))["id"])
    household = await identity_api.household_for_user(db_session, priya_id)
    assert household is not None
    box = await orders_api.create_from_cycle(
        db_session, await approved_cycle(db_session, household, slot_id, "tomato")
    )
    tiffin = await orders_api.create_from_cycle(
        db_session, await approved_cycle(db_session, household, slot_id, "onion")
    )
    tiffin.kind = "tiffin"  # no tiffin ordering path yet (P3); the state machine is what counts
    await db_session.flush()
    box_id, tiffin_id = box.id, tiffin.id
    for order_id in (box_id, tiffin_id):
        await orders_api.transition(db_session, order_id, "locked", actor="system")

    async def for_delivery(kinds: tuple[str, ...] | None) -> set[uuid.UUID]:
        found = await orders_api.orders_for_delivery(db_session, SEED_HUB_ID, DELIVERY, kinds=kinds)
        return {summary.order.id for summary in found}

    assert await for_delivery(None) == {box_id, tiffin_id}  # all kinds, as before
    assert await for_delivery(("tiffin",)) == {tiffin_id}
    assert await for_delivery(("box", "addon")) == {box_id}

    kitchen, rider = uuid.uuid4(), uuid.uuid4()
    with pytest.raises(ProblemError) as packer:
        await orders_api.transition(db_session, tiffin_id, "picking", actor="packer")
    assert packer.value.code == "orders.illegal_transition"
    await orders_api.transition(
        db_session, tiffin_id, "picking", actor="kitchen", actor_user_id=kitchen
    )
    await orders_api.transition(
        db_session, tiffin_id, "packed", actor="kitchen", actor_user_id=kitchen
    )
    await orders_api.transition(
        db_session, tiffin_id, "dispatched", actor="rider", actor_user_id=rider
    )
    with pytest.raises(ProblemError) as no_hold:
        await orders_api.transition(db_session, tiffin_id, "at_society", actor="captain")
    assert no_hold.value.extra == {"kind": "tiffin", "from": "dispatched", "to": "at_society"}
    await orders_api.transition(
        db_session, tiffin_id, "delivered", actor="rider", actor_user_id=rider
    )
    published = (
        await db_session.execute(
            select(OutboxEvent.event_type, OutboxEvent.payload).order_by(OutboxEvent.id)
        )
    ).all()
    assert [(t, p["actor"]) for t, p in published if p.get("order_id") == str(tiffin_id)] == [
        ("orders.locked", "system"),
        ("orders.packed", "kitchen"),
        ("orders.dispatched", "rider"),
        ("orders.delivered", "rider"),
    ]
    assert all(p["kind"] == "tiffin" for _, p in published if p.get("order_id") == str(tiffin_id))
    await db_session.commit()

    # The customer sees which kind of actor moved her tiffin, never who.
    detail = (await api.get(f"/v1/orders/{tiffin_id}", headers=priya)).json()
    assert detail["kind"] == "tiffin" and detail["status"] == "delivered"
    assert [(e["status"], e["actor"]) for e in detail["timeline"]] == [
        ("approved", "customer"),
        ("locked", "system"),
        ("picking", "kitchen"),
        ("packed", "kitchen"),
        ("dispatched", "rider"),
        ("delivered", "rider"),
    ]
    assert all(set(entry) == {"status", "at", "actor"} for entry in detail["timeline"])
    assert str(rider) not in str(detail) and str(kitchen) not in str(detail)
