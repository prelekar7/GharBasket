"""Rider execution (M7 §3.3): the rider starts today's route (packed orders dispatched as the
rider), arrives at and skips stops and completes the route, the bags not handed over riding back
to the hub."""

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
import time_machine
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.hub_ops import api as hub_ops_api
from gharbasket.modules.logistics.models import FieldEvent, Route, RouteStop
from gharbasket.modules.orders.models import Order, OrderEvent
from gharbasket.platform.models import OutboxEvent
from gharbasket.tools.seed import SEED_SOCIETY_ID

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
    SANJAY,
    SOLVED_AT,
    SUNITA,
    VIKRAM,
    as_user,
    key,
    pack,
    published_day,
    user_id,
    vehicle,
)

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("world")]

GREEN_VALLEY = uuid.UUID("0199a000-0000-7000-8000-00000000b002")


async def events(db: AsyncSession, event_type: str) -> list[dict[str, Any]]:
    rows = await db.execute(
        select(OutboxEvent.payload)
        .where(OutboxEvent.event_type == event_type)
        .order_by(OutboxEvent.id)
    )
    return list(rows.scalars())


async def statuses(db: AsyncSession, order_ids: list[uuid.UUID]) -> list[str]:
    rows = await db.execute(
        select(Order.id, Order.status)
        .where(Order.id.in_(order_ids))
        .execution_options(populate_existing=True)
    )
    found = dict(rows.all())
    return [found[o] for o in order_ids]


def by_society(route: dict[str, Any], society_id: uuid.UUID) -> dict[str, Any]:
    [stop] = [s for s in route["stops"] if s["society"]["id"] == str(society_id)]
    return stop


@time_machine.travel(MORNING, tick=False)
async def test_the_rider_starts_the_route_leaving_unpacked_bags_behind(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    day = await published_day(db_session)
    arjun = await user_id(db_session, ARJUN)
    api = clients()
    rider, manager = await as_user(api, google, ARJUN), await as_user(api, google, VIKRAM)
    path = f"/v1/routes/{day.route_id}"

    start = f"{path}/start"
    refused = await api.post(start, json={}, headers={**rider, **key()})
    assert (refused.status_code, refused.json()["code"]) == (409, "logistics.bags_not_packed")
    assert sorted(refused.json()["order_ids"]) == sorted(str(o) for o in day.orders.values())

    await pack(db_session, day.orders[PRIYA], day.orders[RAMESH], day.orders[SUNITA])
    headers = {**rider, **key()}
    started = await api.post(
        start, json={"leave_behind": True, "client_at": "2026-10-13T00:59:00Z"}, headers=headers
    )
    replay = await api.post(
        start, json={"leave_behind": True, "client_at": "2026-10-13T00:59:00Z"}, headers=headers
    )
    assert started.status_code == 200, started.text
    assert replay.json() == started.json() and replay.headers["Idempotent-Replayed"] == "true"
    out = started.json()
    assert (out["status"], out["left_behind"], out["version"]) == (
        "in_progress",
        [str(day.orders[MEENA])],
        3,
    )
    assert all(s["handover_code"] and len(s["handover_code"]) == 4 for s in out["stops"])
    assert by_society(out, SEED_SOCIETY_ID)["bags"] == 1  # Meena's bag stayed behind
    assert all(s["eta_now"] == s["eta"] for s in out["stops"])
    packed = [day.orders[p] for p in (PRIYA, RAMESH, SUNITA)]
    assert await statuses(db_session, packed) == ["dispatched"] * 3
    assert await statuses(db_session, [day.orders[MEENA]]) == ["locked"]
    moves = await db_session.execute(
        select(OrderEvent.actor, OrderEvent.actor_user_id).where(
            OrderEvent.order_id.in_(packed), OrderEvent.to_status == "dispatched"
        )
    )
    assert set(moves.all()) == {("rider", arjun)}
    [tapped] = (await db_session.execute(select(FieldEvent))).scalars()
    assert (tapped.action, tapped.user_id, tapped.client_at) == (
        "start",
        arjun,
        datetime(2026, 10, 13, 0, 59, tzinfo=UTC),
    )

    again = await api.post(start, json={}, headers={**rider, **key()})
    assert (again.status_code, again.json()["code"]) == (409, "logistics.routes_started")
    edit = await api.patch(
        path, json={"rider_user_id": None}, headers={**manager, "If-Match": '"3"'}
    )
    assert (edit.status_code, edit.json()["code"]) == (409, "logistics.routes_started")


@time_machine.travel(MORNING, tick=False)
async def test_arrive_skip_and_complete_send_the_bags_not_handed_over_back_to_the_hub(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    day = await published_day(db_session)
    await pack(db_session, *day.orders.values())
    api = clients()
    rider = await as_user(api, google, ARJUN)
    first, second, third = day.stop_ids

    early = await api.post(f"/v1/stops/{first}/arrive", headers={**rider, **key()})
    assert (early.status_code, early.json()["code"]) == (409, "logistics.route_not_started")
    started = await api.post(f"/v1/routes/{day.route_id}/start", headers={**rider, **key()})
    assert started.status_code == 200, started.text

    with time_machine.travel(MORNING.replace(hour=2), tick=False):  # 07:30 IST
        later_rider = await as_user(api, google, ARJUN)
        arrived = await api.post(
            f"/v1/stops/{first}/arrive", json={}, headers={**later_rider, **key()}
        )
    assert arrived.status_code == 200, arrived.text
    assert (arrived.json()["status"], arrived.json()["eta_now"]) == ("arrived", "07:30")
    stop = await db_session.get(RouteStop, first)
    assert stop is not None
    late_by = stop.delay_s
    again = await api.post(f"/v1/stops/{first}/arrive", json={}, headers={**rider, **key()})
    assert again.json()["arrived_at"] == arrived.json()["arrived_at"]  # once
    [told] = await events(db_session, "logistics.stop_arrived")
    assert told["stop_id"] == str(first) and told["route_id"] == str(day.route_id)
    assert set(told) == {"stop_id", "route_id", "hub_id", "society_id", "captain_user_id", "at"}
    detail = (await api.get(f"/v1/routes/{day.route_id}", headers=rider)).json()
    later = detail["stops"][1]
    hours, minutes = map(int, later["eta"].split(":"))
    moved = hours * 3600 + minutes * 60 + late_by
    assert later["eta_now"] == f"{moved // 3600:02d}:{moved % 3600 // 60:02d}"

    done = await api.post(f"/v1/routes/{day.route_id}/complete", headers={**rider, **key()})
    assert (done.status_code, done.json()["code"]) == (409, "logistics.stops_open")
    assert set(done.json()["stop_ids"]) == {str(s) for s in day.stop_ids}

    for stop_id in day.stop_ids:
        skipped = await api.post(
            f"/v1/stops/{stop_id}/skip",
            json={"reason": "captain_absent", "note": "gate closed"},
            headers={**rider, **key()},
        )
        assert skipped.status_code == 200 and skipped.json()["status"] == "skipped"
    bad = await api.post(
        f"/v1/stops/{second}/skip", json={"reason": "x"}, headers={**rider, **key()}
    )
    assert bad.status_code == 422

    completed = await api.post(
        f"/v1/routes/{day.route_id}/complete",
        json={"empties_returned": 0},
        headers={**rider, **key()},
    )
    assert completed.status_code == 200, completed.text
    assert (completed.json()["status"], completed.json()["stops_done"]) == ("completed", 3)
    ids = list(day.orders.values())
    assert await statuses(db_session, ids) == ["undelivered"] * 4
    failed = await events(db_session, "orders.delivery_failed")
    assert {(e["reason"], e["actor"]) for e in failed} == {("returned_to_hub", "rider")}
    [route_done] = await events(db_session, "logistics.route_completed")
    assert (route_done["stops_done"], route_done["stops_skipped"]) == (0, 3)
    route = await db_session.get(Route, day.route_id, populate_existing=True)
    assert route is not None and route.empties_returned == 0

    for closed in (
        await api.post(f"/v1/stops/{third}/arrive", headers={**rider, **key()}),
        await api.post(f"/v1/routes/{day.route_id}/complete", headers={**rider, **key()}),
    ):
        assert (closed.status_code, closed.json()["code"]) == (409, "logistics.route_closed")


async def test_start_needs_today_the_routes_rider_and_a_free_rider(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    with time_machine.travel(SOLVED_AT, tick=False):
        day = await published_day(db_session)
        await pack(db_session, *day.orders.values())
        api = clients()
        rider = await as_user(api, google, ARJUN)
        tonight = await api.post(f"/v1/routes/{day.route_id}/start", headers={**rider, **key()})
        assert (tonight.status_code, tonight.json()["code"]) == (409, "logistics.route_not_today")

    with time_machine.travel(MORNING, tick=False):
        api = clients()
        rider, manager = await as_user(api, google, ARJUN), await as_user(api, google, VIKRAM)
        vikram, arjun = await user_id(db_session, VIKRAM), await user_id(db_session, ARJUN)
        # Another van's route of the same plan, in progress with Arjun.
        other = await vehicle(db_session, "EV-9")
        route = await db_session.get(Route, day.route_id)
        assert route is not None
        busy = Route(
            plan_id=route.plan_id,
            hub_id=route.hub_id,
            delivery_date=DAY,
            kind="box",
            trip=1,
            vehicle_id=other.id,
            rider_user_id=arjun,
            status="in_progress",
            depart_time=route.depart_time,
            return_by=route.return_by,
            planned_km=1.0,
        )
        db_session.add(busy)
        await db_session.flush()
        busy_id = busy.id
        await db_session.commit()
        refused = await api.post(f"/v1/routes/{day.route_id}/start", headers={**rider, **key()})
        assert (refused.status_code, refused.json()["code"]) == (409, "logistics.rider_busy")
        # The route is now Vikram's: Arjun can neither read nor start it.
        await db_session.execute(
            update(Route).where(Route.id == day.route_id).values(rider_user_id=vikram)
        )
        await db_session.commit()
        hidden = await api.get(f"/v1/routes/{day.route_id}", headers=rider)
        assert (hidden.status_code, hidden.json()["code"]) == (404, "logistics.route_not_found")
        await db_session.execute(
            update(Route).where(Route.id == day.route_id).values(rider_user_id=arjun)
        )
        await db_session.execute(
            update(Route).where(Route.id == busy_id).values(status="completed")
        )
        await db_session.commit()
        # A hub manager starts for the rider: the orders move as Arjun's.
        started = await api.post(f"/v1/routes/{day.route_id}/start", headers={**manager, **key()})
        assert started.status_code == 200, started.text
        actors = await db_session.execute(
            select(OrderEvent.actor, OrderEvent.actor_user_id).where(
                OrderEvent.to_status == "dispatched"
            )
        )
        assert set(actors.all()) == {("rider", arjun)}
        [tapped] = (await db_session.execute(select(FieldEvent))).scalars()
        assert tapped.user_id == vikram


@time_machine.travel(MORNING, tick=False)
async def test_another_riders_route_and_other_roles_are_refused(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    day = await published_day(db_session)
    await pack(db_session, *day.orders.values())
    api = clients()
    rider, packer = await as_user(api, google, ARJUN), await as_user(api, google, KAVITA)
    owner, customer = await as_user(api, google, SANJAY), await as_user(api, google, PRIYA)
    await db_session.execute(
        update(Route).where(Route.id == day.route_id).values(rider_user_id=uuid.uuid4())
    )
    await db_session.commit()

    # Arjun rides for the hub, but this route is another rider's.
    for refused in (
        await api.post(f"/v1/routes/{day.route_id}/start", headers={**rider, **key()}),
        await api.post(f"/v1/stops/{day.stop_ids[0]}/arrive", headers={**rider, **key()}),
    ):
        assert (refused.status_code, refused.json()["code"]) == (403, "logistics.not_your_route")
    for who in (packer, owner, customer):
        start = await api.post(f"/v1/routes/{day.route_id}/start", headers={**who, **key()})
        assert (start.status_code, start.json()["code"]) == (403, "auth.forbidden")
    missing = await api.post(f"/v1/stops/{uuid.uuid4()}/arrive", headers={**rider, **key()})
    assert (missing.status_code, missing.json()["code"]) == (404, "logistics.stop_not_found")
    assert await statuses(db_session, list(day.orders.values())) == ["packed"] * 4


@time_machine.travel(MORNING, tick=False)
async def test_the_start_takes_the_packed_crates_and_bags_from_the_hub(
    clients: ClientFactory,
    google: FakeGoogle,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    day = await published_day(db_session)
    await pack(db_session, *day.orders.values())

    async def loads(*_: object) -> list[hub_ops_api.SocietyLoad]:
        return [hub_ops_api.SocietyLoad(SEED_SOCIETY_ID, bags=5, packed_bags=5, crates=2)]

    monkeypatch.setattr(hub_ops_api, "society_loads", loads)
    api = clients()
    rider = await as_user(api, google, ARJUN)
    started = await api.post(f"/v1/routes/{day.route_id}/start", headers={**rider, **key()})
    assert started.status_code == 200, started.text
    sunrise = by_society(started.json(), SEED_SOCIETY_ID)
    green = by_society(started.json(), GREEN_VALLEY)
    assert (sunrise["bags"], sunrise["crates"]) == (5, 2)  # M6's counts
    assert (green["bags"], green["crates"]) == (1, 1)  # one bag per dispatched order
