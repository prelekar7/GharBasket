"""Triage and the customer's card (M7 §3.4, §3.5): the household follows its order's card
(window, ETA, tracking, captain, code, proof); the hub redelivers once or refunds up to what is
left; the exceptions board pages the hub's deliveries."""

import uuid
from datetime import date
from typing import Any

import pytest
import time_machine
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.logistics.models import Delivery, RoutePlan, RouteStop
from gharbasket.modules.orders.models import Order, OrderEvent
from gharbasket.platform.models import OutboxEvent
from gharbasket.tools.seed import SEED_HUB_ID, SEED_SOCIETY_ID

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .conftest import (
    ARJUN,
    DAY,
    KAVITA,
    MEENA,
    MORNING,
    PRIYA,
    RAMESH,
    SUNITA,
    VIKRAM,
    Day,
    as_user,
    key,
    pack,
    published_day,
    user_id,
)

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("world")]

GREEN_VALLEY = uuid.UUID("0199a000-0000-7000-8000-00000000b002")


def bag(order_id: uuid.UUID) -> str:
    return f"GB1:{order_id.hex}"


async def events(db: AsyncSession, event_type: str) -> list[dict[str, Any]]:
    rows = await db.execute(
        select(OutboxEvent.payload)
        .where(OutboxEvent.event_type == event_type)
        .order_by(OutboxEvent.id)
    )
    return list(rows.scalars())


async def attempts(db: AsyncSession, order_id: uuid.UUID) -> list[Delivery]:
    rows = await db.execute(
        select(Delivery)
        .where(Delivery.order_id == order_id)
        .order_by(Delivery.attempt)
        .execution_options(populate_existing=True)
    )
    return list(rows.scalars())


async def status(db: AsyncSession, order_id: uuid.UUID) -> str:
    found = await db.scalar(
        select(Order.status).where(Order.id == order_id).execution_options(populate_existing=True)
    )
    assert found is not None
    return found


async def started(
    api: Any, google: FakeGoogle, db: AsyncSession
) -> tuple[Day, dict[uuid.UUID | None, tuple[uuid.UUID, str]]]:
    """The day's route started; stops by society as (stop id, handover code)."""
    day = await published_day(db)
    await pack(db, *day.orders.values())
    rider = await as_user(api, google, ARJUN)
    answer = await api.post(f"/v1/routes/{day.route_id}/start", headers={**rider, **key()})
    assert answer.status_code == 200, answer.text
    rows = await db.execute(
        select(RouteStop.society_id, RouteStop.id, RouteStop.handover_code).where(
            RouteStop.route_id == day.route_id
        )
    )
    return day, {society: (stop, code or "") for society, stop, code in rows.all()}


async def hand_over(api: Any, who: dict[str, str], stop: tuple[uuid.UUID, str], bags: int) -> None:
    done = await api.post(
        f"/v1/stops/{stop[0]}/handover",
        json={"crates": 1, "bags": bags, "handover_code": stop[1]},
        headers={**who, **key()},
    )
    assert done.status_code == 200, done.text


@time_machine.travel(MORNING, tick=False)
async def test_the_household_follows_its_card_and_the_captain_delivers_by_scan(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    day, stops = await started(api, google, db_session)
    priya, captain = await as_user(api, google, PRIYA), await as_user(api, google, MEENA)
    manager = await as_user(api, google, VIKRAM)
    order = day.orders[PRIYA]
    path = f"/v1/deliveries/{order}"

    mine = await api.get(path, headers=priya)
    assert mine.status_code == 200, mine.text
    card = mine.json()
    [first] = await attempts(db_session, order)
    assert (card["status"], card["attempt"], card["delivery_code"]) == (
        "on_route",
        1,
        first.delivery_code,
    )
    assert card["tracking"] == {"route_id": str(day.route_id)}
    assert card["window"] == {"start": "07:00", "end": "09:00"} and card["eta"]
    assert card["captain"]["display_name"] and "phone" not in str(card["captain"])
    staff = (await api.get(path, headers=manager)).json()
    assert staff["delivery_code"] is None and staff["status"] == "on_route"
    ramesh = await as_user(api, google, RAMESH)
    hidden = await api.get(path, headers=ramesh)
    assert (hidden.status_code, hidden.json()["code"]) == (404, "logistics.delivery_not_found")

    await hand_over(api, captain, stops[SEED_SOCIETY_ID], 2)
    assert (await api.get(path, headers=priya)).json()["status"] == "with_captain"
    wrong = await api.post(
        f"{path}/proof",
        json={"bag_qr": bag(uuid.uuid4()), "proof": "scan"},
        headers={**captain, **key()},
    )
    assert (wrong.status_code, wrong.json()["code"]) == (422, "logistics.bag_mismatch")
    done = await api.post(
        f"{path}/proof", json={"bag_qr": bag(order), "proof": "scan"}, headers={**captain, **key()}
    )
    assert done.status_code == 200, done.text
    assert (done.json()["status"], done.json()["proof"], done.json()["by"]) == (
        "delivered",
        "scan",
        "captain",
    )
    assert await status(db_session, order) == "delivered"
    [delivered] = await events(db_session, "orders.delivered")
    assert delivered["actor"] == "captain"  # M8 pays her ₹6 a bag on this event (ADR-0027)
    again = await api.post(
        f"{path}/proof", json={"bag_qr": bag(order), "proof": "scan"}, headers={**captain, **key()}
    )
    assert (again.status_code, again.json()["code"]) == (409, "logistics.delivery_closed")
    after = (await api.get(path, headers=priya)).json()
    assert after["proof"]["kind"] == "scan" and after["delivery_code"] is None
    assert after["tracking"] is None


@time_machine.travel(MORNING, tick=False)
async def test_triage_redelivers_once_or_refunds_up_to_what_is_left(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    day, stops = await started(api, google, db_session)
    rider, manager = await as_user(api, google, ARJUN), await as_user(api, google, VIKRAM)
    await hand_over(api, rider, stops[GREEN_VALLEY], 1)
    sunita = day.orders[SUNITA]
    refused = await api.post(
        f"/v1/deliveries/{sunita}/undelivered",
        json={"reason": "refused"},
        headers={**rider, **key()},
    )
    assert refused.json()["status"] == "triage"
    assert await status(db_session, sunita) == "undelivered"

    path = f"/v1/deliveries/{sunita}/resolve"
    order = await db_session.get(Order, sunita)
    assert order is not None
    total = order.total_paise
    too_much = await api.post(
        path,
        json={"outcome": "refund", "refund_paise": total + 1, "note": "x"},
        headers={**manager, **key()},
    )
    assert (too_much.status_code, too_much.json()["code"]) == (422, "logistics.refund_invalid")
    assert too_much.json()["max_paise"] == total
    again = await api.post(
        path, json={"outcome": "redeliver", "note": "she was at work"}, headers={**manager, **key()}
    )
    assert again.status_code == 200, again.text
    assert (again.json()["attempt"], again.json()["status"], again.json()["delivery_date"]) == (
        2,
        "scheduled",
        "2026-10-14",
    )
    assert await status(db_session, sunita) == "redelivery"
    [scheduled] = await events(db_session, "logistics.redelivery_scheduled")
    assert scheduled["delivery_date"] == "2026-10-14" and scheduled["window"]["start"] == "09:00"
    closed = await api.post(
        path, json={"outcome": "refund", "note": "x"}, headers={**manager, **key()}
    )
    assert (closed.status_code, closed.json()["code"]) == (409, "logistics.not_undelivered")
    card = (await api.get(f"/v1/deliveries/{sunita}", headers=manager)).json()
    assert card["redelivery"] == {
        "date": "2026-10-14",
        "window": {"start": "09:00", "end": "11:00"},
    }

    # The hub marks Ramesh's bag undelivered for another reason, then refunds part of it.
    ramesh = day.orders[RAMESH]
    krishna = [s for society, s in stops.items() if society not in (SEED_SOCIETY_ID, GREEN_VALLEY)]
    await hand_over(api, manager, krishna[0], 1)
    marked = await api.post(
        f"/v1/deliveries/{ramesh}/undelivered",
        json={"reason": "other", "note": "gate locked"},
        headers={**manager, **key()},
    )
    assert marked.json()["status"] == "triage"
    part = await api.post(
        f"/v1/deliveries/{ramesh}/resolve",
        json={"outcome": "refund", "refund_paise": 1000, "note": "half"},
        headers={**manager, **key()},
    )
    # `refunded` is final, so less than all that is left needs final: true; nothing moved before.
    assert (part.status_code, part.json()["code"]) == (422, "logistics.partial_refund_not_final")
    assert (await attempts(db_session, ramesh))[0].status == "triage"
    part = await api.post(
        f"/v1/deliveries/{ramesh}/resolve",
        json={"outcome": "refund", "refund_paise": 1000, "final": True, "note": "half"},
        headers={**manager, **key()},
    )
    assert part.status_code == 200, part.text
    assert (part.json()["status"], (await attempts(db_session, ramesh))[0].refund_paise) == (
        "refunded",
        1000,
    )
    packer = await as_user(api, google, KAVITA)
    denied = await api.post(
        path, json={"outcome": "refund", "note": "x"}, headers={**packer, **key()}
    )
    assert denied.status_code == 403


@time_machine.travel(MORNING, tick=False)
async def test_the_exceptions_board_pages_the_hubs_deliveries(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    day, _ = await started(api, google, db_session)
    manager, packer = await as_user(api, google, VIKRAM), await as_user(api, google, KAVITA)
    path = f"/v1/hubs/{SEED_HUB_ID}/deliveries"
    first = await api.get(path, params={"date": DAY.isoformat(), "limit": 3}, headers=manager)
    assert first.status_code == 200, first.text
    assert len(first.json()["items"]) == 3 and first.json()["next_cursor"]
    rest = await api.get(
        path,
        params={"date": DAY.isoformat(), "cursor": first.json()["next_cursor"]},
        headers=manager,
    )
    assert len(rest.json()["items"]) == 1 and rest.json()["next_cursor"] is None
    seen = {i["order_id"] for i in first.json()["items"] + rest.json()["items"]}
    assert seen == {str(o) for o in day.orders.values()}
    none = await api.get(path, params={"status": "held"}, headers=manager)
    assert none.json()["items"] == []
    assert (await api.get(path, headers=packer)).status_code == 403
    bad = await api.get(path, params={"cursor": "nonsense"}, headers=manager)
    assert bad.status_code == 422
    assert await user_id(db_session, VIKRAM)


@time_machine.travel(MORNING, tick=False)
async def test_a_redelivery_waits_for_an_unsolved_day_and_the_board_flags_what_no_van_takes(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    day = await published_day(db_session)
    await pack(db_session, *[o for p, o in day.orders.items() if p != MEENA])
    rider, manager = await as_user(api, google, ARJUN), await as_user(api, google, VIKRAM)
    started = await api.post(
        f"/v1/routes/{day.route_id}/start", json={"leave_behind": True}, headers={**rider, **key()}
    )
    assert started.status_code == 200, started.text
    board = (await api.get(f"/v1/hubs/{SEED_HUB_ID}/deliveries", headers=manager)).json()
    assert board["left_behind"] == [str(day.orders[MEENA])]  # Meena's bag was not packed

    stops = dict(
        (
            await db_session.execute(
                select(RouteStop.society_id, RouteStop.id).where(RouteStop.route_id == day.route_id)
            )
        ).all()
    )
    green = stops[GREEN_VALLEY]
    done = await api.post(
        f"/v1/stops/{green}/handover", json={"crates": 1, "bags": 1}, headers={**rider, **key()}
    )
    assert done.status_code == 200, done.text
    sunita = day.orders[SUNITA]
    await api.post(
        f"/v1/deliveries/{sunita}/undelivered",
        json={"reason": "refused"},
        headers={**rider, **key()},
    )
    # Tomorrow's plan is already published: the redelivery goes on the day after.
    db_session.add(
        RoutePlan(
            hub_id=SEED_HUB_ID,
            delivery_date=date(2026, 10, 14),
            kind="box",
            status="published",
            trigger="job",
        )
    )
    await db_session.commit()
    again = await api.post(
        f"/v1/deliveries/{sunita}/resolve",
        json={"outcome": "redeliver", "note": "next free day"},
        headers={**manager, **key()},
    )
    assert again.status_code == 200, again.text
    assert again.json()["delivery_date"] == "2026-10-15"
    # Booked on a day whose plan went out without it: the board flags it.
    await db_session.execute(
        update(Delivery)
        .where(Delivery.order_id == sunita, Delivery.attempt == 2)
        .values(delivery_date=date(2026, 10, 14))
    )
    await db_session.commit()
    flagged = await api.get(
        f"/v1/hubs/{SEED_HUB_ID}/deliveries", params={"date": "2026-10-14"}, headers=manager
    )
    assert flagged.json()["orphans"] == [str(sunita)]


@time_machine.travel(MORNING, tick=False)
async def test_a_triage_refund_names_the_manager(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    day, stops = await started(api, google, db_session)
    rider, manager = await as_user(api, google, ARJUN), await as_user(api, google, VIKRAM)
    await hand_over(api, rider, stops[GREEN_VALLEY], 1)
    sunita = day.orders[SUNITA]
    await api.post(
        f"/v1/deliveries/{sunita}/undelivered",
        json={"reason": "refused"},
        headers={**rider, **key()},
    )
    refunded = await api.post(
        f"/v1/deliveries/{sunita}/resolve",
        json={"outcome": "refund", "note": "refused at the door"},
        headers={**manager, **key()},
    )
    assert refunded.status_code == 200, refunded.text
    moved = await db_session.execute(
        select(OrderEvent.actor, OrderEvent.actor_user_id).where(
            OrderEvent.order_id == sunita, OrderEvent.to_status == "refunded"
        )
    )
    assert moved.all() == [("system", await user_id(db_session, VIKRAM))]
