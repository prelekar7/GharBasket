"""Proof at the door (M7 §3.4): the bag must be the order's; a captain proves it by the scan, a
rider in rider mode by the household's code or a photo; wrong codes are counted."""

import uuid
from typing import Any

import pytest
import time_machine
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.logistics.models import Delivery, RouteStop
from gharbasket.modules.orders.models import Order, OrderEvent
from gharbasket.platform.models import OutboxEvent
from gharbasket.tools.seed import SEED_SOCIETY_ID

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .conftest import (
    ARJUN,
    MEENA,
    MORNING,
    PRIYA,
    SUNITA,
    Day,
    as_user,
    key,
    pack,
    published_day,
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
async def test_the_captain_delivers_by_scan_after_the_handover(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    day, stops = await started(api, google, db_session)
    captain = await as_user(api, google, MEENA)
    order = day.orders[PRIYA]
    path = f"/v1/deliveries/{order}"

    await hand_over(api, captain, stops[SEED_SOCIETY_ID], 2)
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


@time_machine.travel(MORNING, tick=False)
async def test_a_rider_in_rider_mode_needs_the_households_code_or_a_photo(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    day, stops = await started(api, google, db_session)
    rider = await as_user(api, google, ARJUN)
    order = day.orders[SUNITA]
    await hand_over(api, rider, stops[GREEN_VALLEY], 1)
    path = f"/v1/deliveries/{order}/proof"
    scan = await api.post(
        path, json={"bag_qr": bag(order), "proof": "scan"}, headers={**rider, **key()}
    )
    assert (scan.status_code, scan.json()["code"]) == (422, "logistics.proof_required")
    [first] = await attempts(db_session, order)
    code = first.delivery_code
    body = {
        "bag_qr": bag(order),
        "proof": "otp",
        "delivery_code": "0000" if code != "0000" else "1111",
    }
    headers = {**rider, **key()}
    wrong = await api.post(path, json=body, headers=headers)
    assert (wrong.status_code, wrong.json()["code"]) == (422, "logistics.code_invalid")
    assert (await api.post(path, json=body, headers=headers)).status_code == 422  # replay
    assert (await attempts(db_session, order))[0].code_attempts == 1
    photo = await api.post(
        path,
        json={
            "bag_qr": bag(order),
            "proof": "photo",
            "photo_key": f"delivery_photo/{uuid.uuid4()}/x.jpg",
        },
        headers={**rider, **key()},
    )
    assert (photo.status_code, photo.json()["code"]) == (422, "logistics.photo_key_invalid")
    done = await api.post(path, json={**body, "delivery_code": code}, headers={**rider, **key()})
    assert done.status_code == 200, done.text
    assert (done.json()["proof"], done.json()["by"]) == ("otp", "rider")
    moves = await db_session.execute(
        select(OrderEvent.actor).where(
            OrderEvent.order_id == order, OrderEvent.to_status == "delivered"
        )
    )
    assert list(moves.scalars()) == ["rider"]
