"""Packing follows the published routes (M6 §3.2): on `logistics.route_published`, and when tasks
are built after M7 published, queued tasks take their route and stop and the loading order (route
start, stop order, tower, flat; tasks on no route after them, in their old order). Started tasks
keep their place."""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import httpx2
import pytest
import structlog
import time_machine
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.hub_ops import jobs as hub_jobs
from gharbasket.modules.hub_ops import packing
from gharbasket.modules.hub_ops.models import PackingTask
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.logistics import plans
from gharbasket.modules.logistics.models import Route, RoutePlan, RouteStop, Vehicle
from gharbasket.platform.models import OutboxEvent
from gharbasket.platform.outbox import relay
from gharbasket.tools.seed import SEED_HUB_ID

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from ..logistics.conftest import (
    ARJUN,
    DAY,
    MEENA,
    PRIYA,
    RAMESH,
    SUNITA,
    VIKRAM,
    as_user,
    key,
    locked_order,
    slot,
    user_id,
    world,  # noqa: F401
)

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("world")]

NOW = datetime(2026, 10, 12, 23, 45, tzinfo=UTC)  # Tue 13 Oct, 05:15 IST
TASKS = f"/v1/hubs/{SEED_HUB_ID}/packing-tasks"
Drop = tuple[uuid.UUID, uuid.UUID]  # (society, slot)


async def society_of(db: AsyncSession, phone: str) -> uuid.UUID:
    household = await identity_api.household_for_user(db, await user_id(db, phone))
    assert household is not None
    return household.society_id


async def published_plan(
    db: AsyncSession,
    routes: list[tuple[time, list[Drop]]],
    *,
    hub_id: uuid.UUID = SEED_HUB_ID,
    day: date = DAY,
) -> tuple[RoutePlan, list[Route]]:
    """A published box plan with these routes (departure, drops in stop order), each on its own
    van, as M7's 23:15 job leaves it; committed. No event is sent."""
    plan = RoutePlan(
        hub_id=hub_id, delivery_date=day, kind="box", trigger="job", status="published"
    )
    db.add(plan)
    await db.flush()
    made: list[Route] = []
    for depart, drops in routes:
        van = Vehicle(
            hub_id=hub_id,
            label=f"V-{uuid.uuid4().hex[:6]}",
            kind="cargo_3w",
            capacity_kg=400,
            capacity_crates=24,
            capacity_tiffins=0,
        )
        db.add(van)
        await db.flush()
        route = Route(
            plan_id=plan.id,
            hub_id=hub_id,
            delivery_date=day,
            kind="box",
            trip=1,
            vehicle_id=van.id,
            status="published",
            depart_time=depart,
            return_by=time(11),
            planned_km=12.0,
        )
        db.add(route)
        await db.flush()
        db.add_all(
            RouteStop(route_id=route.id, seq=n, society_id=society, slot_id=slot_id, eta=depart)
            for n, (society, slot_id) in enumerate(drops, 1)
        )
        made.append(route)
    await db.commit()
    return plan, made


async def announce(db: AsyncSession, plan: RoutePlan, *routes: Route, previous: Any = None) -> None:
    """M7's `logistics.route_published` for each route, relayed to its consumers."""
    for route in routes:
        await plans.route_published(db, plan, route, previous)
    await db.commit()
    while (await relay(db)).sent:
        pass


async def build(api: httpx2.AsyncClient, manager: dict[str, str]) -> dict[str, Any]:
    built = await api.post(
        f"{TASKS}/build", json={"date": DAY.isoformat()}, headers={**manager, **key()}
    )
    assert built.status_code == 200, built.text
    return built.json()


async def queue(api: httpx2.AsyncClient, auth: dict[str, str]) -> list[tuple[str, int, Any, int]]:
    """(order id, seq, route, version) by seq."""
    listed = await api.get(TASKS, params={"date": DAY.isoformat()}, headers=auth)
    assert listed.status_code == 200, listed.text
    return [(t["order_id"], t["seq"], t["route"], t["version"]) for t in listed.json()["items"]]


def on(route: Route, stop_seq: int) -> dict[str, Any]:
    return {"route_id": str(route.id), "stop_seq": stop_seq}


async def start(api: httpx2.AsyncClient, manager: dict[str, str], order_id: uuid.UUID) -> None:
    [task_id] = [
        t["id"]
        for t in (await api.get(TASKS, params={"date": DAY.isoformat()}, headers=manager)).json()[
            "items"
        ]
        if t["order_id"] == str(order_id)
    ]
    started = await api.post(
        f"/v1/packing-tasks/{task_id}/start", json={}, headers={**manager, **key()}
    )
    assert started.status_code == 200, started.text


@time_machine.travel(NOW, tick=False)
async def test_published_routes_resequence_queued_tasks_and_started_ones_keep_their_place(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    db, api = db_session, clients()
    manager = await as_user(api, google, VIKRAM)
    six, seven, eight = [await slot(db, time(h)) for h in (6, 7, 8)]
    sunrise, krishna, green = [await society_of(db, p) for p in (PRIYA, RAMESH, SUNITA)]
    ramesh = await locked_order(db, RAMESH, six, DAY)
    sunita = await locked_order(db, SUNITA, seven, DAY)
    meena = await locked_order(db, MEENA, seven, DAY)  # Sunrise Towers, B-402
    await build(api, manager)
    priya = await locked_order(db, PRIYA, seven, DAY)  # Sunrise Towers, A-1203
    late = await locked_order(db, SUNITA, eight, DAY)  # a slot no route serves
    await build(api, manager)
    before = await queue(api, manager)
    assert [(o, s, r) for o, s, r, _ in before] == [
        (str(ramesh), 1, None),
        (str(sunita), 2, None),
        (str(meena), 3, None),
        (str(priya), 4, None),
        (str(late), 5, None),
    ]
    await start(api, manager, ramesh)

    # Van A leaves first: Sunrise Towers, then Krishna Kunj; van B takes Green Valley.
    plan, (a, b) = await published_plan(
        db, [(time(6), [(sunrise, seven), (krishna, six)]), (time(6, 30), [(green, seven)])]
    )
    await announce(db, plan, a, b)

    after = await queue(api, manager)
    assert [(o, s, r) for o, s, r, _ in after] == [
        (str(ramesh), 1, None),  # started: keeps its place (and takes no route)
        (str(priya), 2, on(a, 1)),  # one stop: A wing before B wing
        (str(meena), 3, on(a, 1)),
        (str(sunita), 4, on(b, 1)),
        (str(late), 5, None),  # on no route: after the routed ones, in the old order
    ]
    versions = {o: v for o, _, _, v in after}
    assert versions[str(meena)] == 2 and versions[str(late)] == 1  # bumped only when changed

    # A new rider re-publishes route A; the same event replayed: nothing changes.
    a.rider_user_id = await user_id(db, ARJUN)
    await announce(db, plan, a)
    [payload, *_] = (
        await db.scalars(
            select(OutboxEvent.payload)
            .where(
                OutboxEvent.event_type == "logistics.route_published",
                OutboxEvent.payload["route_id"].astext == str(a.id),
            )
            .order_by(OutboxEvent.id)
        )
    ).all()
    await hub_jobs.on_route_published(
        db, OutboxEvent(event_type="logistics.route_published", payload=payload)
    )
    assert await packing.follow_routes(db, SEED_HUB_ID, DAY) == 0
    await db.commit()
    assert await queue(api, manager) == after

    # Priya's bag is started; a re-solve then puts Green Valley before Sunrise Towers.
    await start(api, manager, priya)
    plan.status = "superseded"
    for old in (a, b):
        old.status = "cancelled"
    await db.commit()
    new_plan, (c,) = await published_plan(
        db, [(time(6), [(green, seven), (sunrise, seven), (krishna, six)])]
    )
    await announce(db, new_plan, c)

    assert [(o, s, r) for o, s, r, _ in await queue(api, manager)] == [
        (str(ramesh), 1, None),
        (str(priya), 2, on(a, 1)),  # started under the old plan: unchanged
        (str(sunita), 3, on(c, 1)),
        (str(meena), 4, on(c, 2)),
        (str(late), 5, None),
    ]


@time_machine.travel(NOW, tick=False)
async def test_a_build_after_the_routes_follows_them_and_other_hubs_and_dates_are_untouched(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    db, api = db_session, clients()
    manager = await as_user(api, google, VIKRAM)
    six, seven = await slot(db, time(6)), await slot(db, time(7))
    sunrise, krishna, green = [await society_of(db, p) for p in (PRIYA, RAMESH, SUNITA)]
    ramesh = await locked_order(db, RAMESH, six, DAY)  # first by slot, but on no route
    priya = await locked_order(db, PRIYA, seven, DAY)
    sunita = await locked_order(db, SUNITA, seven, DAY)
    tomorrow = PackingTask(
        hub_id=SEED_HUB_ID,
        order_id=uuid.uuid4(),
        order_kind="box",
        delivery_date=DAY + timedelta(days=1),
        slot_id=seven,
        society_id=green,
        seq=7,
    )
    db.add(tomorrow)
    await db.commit()
    # Routes of another hub, and of the seed hub's next day, put Krishna Kunj first.
    await published_plan(db, [(time(5), [(krishna, six)])], hub_id=uuid.uuid4())
    await published_plan(
        db, [(time(5), [(krishna, six), (green, seven)])], day=tomorrow.delivery_date
    )
    _, (route,) = await published_plan(db, [(time(6), [(green, seven), (sunrise, seven)])])

    assert await build(api, manager) == {"created": 3, "existing": 0}

    assert [(o, s, r) for o, s, r, _ in await queue(api, manager)] == [
        (str(sunita), 1, on(route, 1)),
        (str(priya), 2, on(route, 2)),
        (str(ramesh), 3, None),
    ]
    await db.refresh(tomorrow)
    assert (tomorrow.seq, tomorrow.route_id, tomorrow.stop_seq, tomorrow.version) == (
        7,
        None,
        None,
        1,
    )


@time_machine.travel(NOW, tick=False)
async def test_an_unreadable_payload_or_unknown_hub_is_logged_and_skipped_and_tiffins_ignored(
    db_session: AsyncSession,
) -> None:
    day = DAY.isoformat()
    unreadable = [
        {},
        ["not", "an", "object"],
        {"kind": "box", "hub_id": "not-a-uuid", "delivery_date": day},
        {"kind": "box", "hub_id": str(SEED_HUB_ID), "delivery_date": "13/10/2026"},
    ]
    with structlog.testing.capture_logs() as logs:
        for payload in unreadable:
            await hub_jobs.on_route_published(
                db_session, OutboxEvent(event_type="logistics.route_published", payload=payload)
            )
        unknown = {"kind": "box", "hub_id": str(uuid.uuid4()), "delivery_date": day}
        tiffin = {"kind": "tiffin", "hub_id": str(uuid.uuid4()), "delivery_date": day}
        for payload in (unknown, tiffin):
            await hub_jobs.on_route_published(
                db_session, OutboxEvent(event_type="logistics.route_published", payload=payload)
            )

    assert [
        (e["event"], e["error"]) for e in logs if e["event"] == "route_published_unreadable"
    ] == [
        ("route_published_unreadable", "KeyError"),
        ("route_published_unreadable", "TypeError"),
        ("route_published_unreadable", "ValueError"),
        ("route_published_unreadable", "ValueError"),
    ]
    skipped = [e for e in logs if e["event"] == "route_published_skipped"]
    assert [e["code"] for e in skipped] == ["hub_ops.hub_not_found"]  # the tiffin route: no log
