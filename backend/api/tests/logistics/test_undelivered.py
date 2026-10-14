"""Undelivered (M7 §3.4): a captain holds a not-home bag until 20:00 and can still deliver it;
a rider's not-home bag rides back; damaged is refunded in full at once through M4; refused waits
for the hub's triage."""

import uuid
from typing import Any

import pytest
import time_machine
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing.models import JournalEntry
from gharbasket.modules.logistics.models import Delivery, RouteStop
from gharbasket.modules.orders.models import Order
from gharbasket.platform.models import OutboxEvent
from gharbasket.tools.seed import SEED_SOCIETY_ID

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .conftest import (
    ARJUN,
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
async def test_not_home_is_held_by_the_captain_and_damaged_is_refunded_at_once(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    day, stops = await started(api, google, db_session)
    captain = await as_user(api, google, MEENA)
    await hand_over(api, captain, stops[SEED_SOCIETY_ID], 2)
    meena_order, priya_order = day.orders[MEENA], day.orders[PRIYA]

    other = await api.post(
        f"/v1/deliveries/{meena_order}/undelivered",
        json={"reason": "other"},
        headers={**captain, **key()},
    )
    assert other.status_code == 422  # a note is required for other
    held = await api.post(
        f"/v1/deliveries/{meena_order}/undelivered",
        json={"reason": "not_home"},
        headers={**captain, **key()},
    )
    assert held.status_code == 200, held.text
    assert (held.json()["status"], held.json()["hold_until"]) == ("held", "20:00")
    assert await status(db_session, meena_order) == "at_society"
    [told] = await events(db_session, "logistics.delivery_held")
    assert told == {
        "order_id": str(meena_order),
        "household_id": told["household_id"],
        "society_id": str(SEED_SOCIETY_ID),
        "hold_until": "20:00",
    }
    today = (await api.get("/v1/captains/me/today", headers=captain)).json()
    assert today["counts"]["held"] == 1
    [flat] = [f for f in today["flats"] if f["order_id"] == str(meena_order)]
    assert (flat["status"], flat["hold_until"]) == ("held", "20:00")
    # The family collected it the same evening.
    collected = await api.post(
        f"/v1/deliveries/{meena_order}/proof",
        json={"bag_qr": bag(meena_order), "proof": "scan"},
        headers={**captain, **key()},
    )
    assert collected.status_code == 200 and collected.json()["status"] == "delivered"

    order = await db_session.get(Order, priya_order)
    assert order is not None
    total = order.total_paise
    damaged = await api.post(
        f"/v1/deliveries/{priya_order}/undelivered",
        json={"reason": "damaged", "note": "crushed"},
        headers={**captain, **key()},
    )
    assert damaged.status_code == 200, damaged.text
    assert damaged.json()["status"] == "refunded"
    assert await status(db_session, priya_order) == "refunded"
    [attempt] = await attempts(db_session, priya_order)
    assert attempt.refund_paise == total
    [failed] = await events(db_session, "orders.delivery_failed")
    assert (failed["reason"], failed["actor"]) == ("damaged", "captain")
    entry = await db_session.scalar(
        select(JournalEntry.details).where(
            JournalEntry.idempotency_key == f"refund:{priya_order}:damaged"
        )
    )
    assert entry is not None and entry["reason"] == "damaged"  # back to her wallet


@time_machine.travel(MORNING, tick=False)
async def test_a_riders_not_home_rides_back_and_refused_goes_to_triage(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    day, stops = await started(api, google, db_session)
    rider, manager = await as_user(api, google, ARJUN), await as_user(api, google, VIKRAM)
    await hand_over(api, rider, stops[GREEN_VALLEY], 1)
    sunita, ramesh = day.orders[SUNITA], day.orders[RAMESH]
    back = await api.post(
        f"/v1/deliveries/{sunita}/undelivered",
        json={"reason": "not_home"},
        headers={**rider, **key()},
    )
    assert back.status_code == 200, back.text
    assert (back.json()["status"], back.json()["reason"], back.json()["by"]) == (
        "undelivered",
        "not_home",
        "rider",
    )
    assert await status(db_session, sunita) == "undelivered"
    krishna = [s for society, s in stops.items() if society not in (SEED_SOCIETY_ID, GREEN_VALLEY)]
    await hand_over(api, manager, krishna[0], 1)
    refused = await api.post(
        f"/v1/deliveries/{ramesh}/undelivered",
        json={"reason": "refused"},
        headers={**manager, **key()},
    )
    assert refused.json()["status"] == "triage"
    closed = await api.post(
        f"/v1/deliveries/{sunita}/undelivered",
        json={"reason": "refused"},
        headers={**rider, **key()},
    )
    assert (closed.status_code, closed.json()["code"]) == (409, "orders.illegal_transition")
