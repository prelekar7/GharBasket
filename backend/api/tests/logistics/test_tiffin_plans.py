"""Tiffin plans (M7 §3.7): at 09:35 `solve_tiffin_routes` routes each hub's lunch tiffins from M8's
assignments in M7, kitchens first and then the societies, on two-wheelers filled by portions
without splitting a kitchen; a clean plan is published, any other waits for review."""

import dataclasses
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
import time_machine
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.logistics import tiffin_plans
from gharbasket.modules.logistics.models import Route, RoutePlan, RouteStop, StopOrder, Vehicle
from gharbasket.modules.logistics.routing import Trip
from gharbasket.modules.partners import api as partners_api
from gharbasket.modules.partners.models import Kitchen
from gharbasket.platform.jobs import run_job
from gharbasket.platform.models import OutboxEvent
from gharbasket.tools.seed import SEED_HUB_ID, SEED_SOCIETY_ID

from ..conftest import make_settings
from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .conftest import ARJUN, MEENA, PRIYA, RAMESH, SUNITA, VIKRAM, as_user, key, user_id, vehicle
from .tiffin_day import (
    ASHA,
    KRISHNA,
    LATA,
    REKHA,
    SOLVE_AT,
    SUNRISE,
    T,
    assign,
    kitchen,
    lunch_slot,
    tiffin,
)

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("world")]

SAVITA = "+919800001012"  # Krishna Kunj's captain, who lives there
KRISHNA_KUNJ = uuid.UUID("0199a000-0000-7000-8000-00000000b005")
GREEN_VALLEY = uuid.UUID("0199a000-0000-7000-8000-00000000b002")


async def solve(db: AsyncSession) -> dict[str, Any]:
    run = await run_job(
        db, "solve_tiffin_routes", settings=make_settings(), scheduled_for=SOLVE_AT, http=None
    )
    assert run.status == "ok" and run.summary is not None, run
    return run.summary


async def two_wheeler(db: AsyncSession, label: str = "TW-1", tiffins: int = 30) -> Vehicle:
    made = await vehicle(
        db, label, kg=40, crates=0, kind="two_wheeler", rider=await user_id(db, ARJUN)
    )
    made.capacity_tiffins = tiffins
    await db.commit()
    return made


async def the_plan(db: AsyncSession) -> RoutePlan:
    found = await db.scalar(
        select(RoutePlan)
        .where(RoutePlan.kind == "tiffin")
        .order_by(RoutePlan.id.desc())
        .execution_options(populate_existing=True)
    )
    assert found is not None
    return found


async def stops_by_route(db: AsyncSession, plan_id: uuid.UUID) -> list[list[RouteStop]]:
    routes = await db.execute(
        select(Route.id).where(Route.plan_id == plan_id).order_by(Route.depart_time)
    )
    out = []
    for route_id in routes.scalars():
        found = await db.execute(
            select(RouteStop).where(RouteStop.route_id == route_id).order_by(RouteStop.seq)
        )
        out.append(list(found.scalars()))
    return out


async def on_stop(db: AsyncSession, stop_id: uuid.UUID) -> set[uuid.UUID]:
    rows = await db.execute(select(StopOrder.order_id).where(StopOrder.stop_id == stop_id))
    return set(rows.scalars())


async def events(db: AsyncSession, event_type: str) -> list[dict[str, Any]]:
    rows = await db.execute(
        select(OutboxEvent.payload)
        .where(OutboxEvent.event_type == event_type)
        .order_by(OutboxEvent.id)
    )
    return list(rows.scalars())


@time_machine.travel(SOLVE_AT, tick=False)
async def test_the_job_routes_kitchens_then_societies_and_publishes_a_clean_plan(
    db_session: AsyncSession,
) -> None:
    lunch = await lunch_slot(db_session)
    lata = await kitchen(db_session, LATA, "Lata tai", at=SUNRISE)
    asha = await kitchen(db_session, ASHA, "Asha tai", at=KRISHNA)
    priya, ramesh = (
        await tiffin(db_session, PRIYA, lunch, 2),
        await tiffin(db_session, RAMESH, lunch, 1),
    )
    meena = await tiffin(db_session, MEENA, lunch, 1)
    at_lata = await assign(db_session, lata, lunch, {priya: 2, ramesh: 1})
    at_asha = await assign(db_session, asha, lunch, {meena: 1})
    await two_wheeler(db_session)

    ran = await solve(db_session)

    assert (ran["plans"], ran["published"], ran["delivery_date"]) == (1, 1, T.isoformat())
    plan = await the_plan(db_session)
    assert (plan.status, plan.trigger, plan.source, plan.model, plan.distance_source) == (
        "published",
        "job",
        "nearest",
        "tiffin-nn-v0",
        "haversine",
    )
    assert plan.summary is not None
    assert (plan.summary["orders"], plan.summary["stops"], plan.summary["routes"]) == (3, 4, 1)
    assert plan.summary["problems"] == []
    [stops] = await stops_by_route(db_session, plan.id)
    assert [s.kind for s in stops] == ["kitchen", "kitchen", "drop", "drop"]
    kitchens = {s.assignment_id: s for s in stops[:2]}
    assert set(kitchens) == {at_lata, at_asha}
    assert kitchens[at_lata].partner_id == lata and kitchens[at_lata].society_id is None
    assert (kitchens[at_lata].portions, kitchens[at_lata].bags, kitchens[at_lata].crates) == (
        3,
        2,
        0,
    )
    assert str(kitchens[at_lata].window_start) == "11:30:00"  # ready_by: the slot start - 1 h
    assert await on_stop(db_session, kitchens[at_lata].id) == {priya, ramesh}
    drops = {s.society_id: s for s in stops[2:]}
    assert set(drops) == {SEED_SOCIETY_ID, KRISHNA_KUNJ}
    # Both kitchens' tiffins for Sunrise Towers ride to one drop, after both kitchens.
    assert await on_stop(db_session, drops[SEED_SOCIETY_ID].id) == {priya, meena}
    assert (drops[SEED_SOCIETY_ID].portions, drops[SEED_SOCIETY_ID].slot_id) == (3, lunch)
    assert not drops[SEED_SOCIETY_ID].rider_mode  # Meena is its captain
    assert all(s.eta >= stops[0].eta for s in stops)

    [told] = await events(db_session, "logistics.route_published")
    assert told["kind"] == "tiffin"
    assert [(s["kind"], s["partner_id"]) for s in told["stops"][:2]] == [
        ("kitchen", str(kitchens[stops[0].assignment_id].partner_id)),
        ("kitchen", str(kitchens[stops[1].assignment_id].partner_id)),
    ]
    assert all(s["society_id"] is None for s in told["stops"][:2])
    assert {s["partner_id"] for s in told["stops"][2:]} == {None}
    assert (await solve(db_session))["skipped"] == 1  # a second run solves nothing again


@time_machine.travel(SOLVE_AT, tick=False)
async def test_trips_fill_by_portions_never_split_a_kitchen_and_a_society_gets_a_drop_on_each(
    db_session: AsyncSession,
) -> None:
    lunch = await lunch_slot(db_session)
    lata = await kitchen(db_session, LATA, "Lata tai", at=SUNRISE)
    asha = await kitchen(db_session, ASHA, "Asha tai", at=KRISHNA)
    priya, ramesh = (
        await tiffin(db_session, PRIYA, lunch, 2),
        await tiffin(db_session, RAMESH, lunch, 1),
    )
    meena, savita = (
        await tiffin(db_session, MEENA, lunch, 1),
        await tiffin(db_session, SAVITA, lunch, 1),
    )
    await assign(db_session, lata, lunch, {priya: 2, ramesh: 1})
    await assign(db_session, asha, lunch, {meena: 1, savita: 1})
    await two_wheeler(db_session, tiffins=3)
    await two_wheeler(db_session, "TW-2", tiffins=30)  # the fleet takes its smallest capacity

    await solve(db_session)

    plan = await the_plan(db_session)
    assert plan.status == "published", plan.summary and plan.summary["problems"]
    first, second = await stops_by_route(db_session, plan.id)
    for stops in (first, second):  # one kitchen each, then its two societies
        assert [s.kind for s in stops] == ["kitchen", "drop", "drop"]
        assert {s.society_id for s in stops[1:]} == {SEED_SOCIETY_ID, KRISHNA_KUNJ}
    together = {frozenset(await on_stop(db_session, s.id)) for s in first[1:] + second[1:]}
    assert together == {
        frozenset({priya}),
        frozenset({ramesh}),
        frozenset({meena}),
        frozenset({savita}),
    }
    routes = (
        await db_session.execute(select(Route).where(Route.plan_id == plan.id).order_by(Route.trip))
    ).scalars()
    one, two = list(routes)
    assert (one.trip, two.trip) == (1, 1) and one.vehicle_id != two.vehicle_id


async def test_one_vehicles_second_trip_leaves_when_its_first_is_back() -> None:
    society, slot = uuid.uuid4(), uuid.uuid4()
    kitchens = [
        tiffin_plans.Place(
            f"kitchen/{name}",
            "kitchen",
            SUNRISE,
            (41_400, 43_200),
            tuple(tiffin_plans.Tiffin(uuid.uuid4(), f"T-{name}", n, society, slot) for n in (2, 1)),
            assignment_id=uuid.uuid4(),
        )
        for name in ("a", "b")
    ]

    trips, places, problems = tiffin_plans.plan_trips(
        kitchens, {society: KRISHNA}, {slot: (45_000, 48_600)}, 3, 1, SUNRISE
    )

    assert problems == []
    first, second = trips
    assert [p.split("/")[0] for p in first.stops] == ["kitchen", str(society)]
    assert (second.van, second.start_s) == (0, first.end_s)
    assert places[second.stops[1]].tiffins == kitchens[1].tiffins  # each trip its own drop
    assert (
        tiffin_plans.check(
            trips, places, kitchens, [Vehicle(label="TW-1", capacity_tiffins=3)], 86_000
        )
        == []
    )


@time_machine.travel(SOLVE_AT, tick=False)
async def test_an_over_capacity_kitchen_rides_alone_and_the_plan_waits_for_review(
    db_session: AsyncSession,
) -> None:
    lunch = await lunch_slot(db_session)
    rekha = await kitchen(db_session, REKHA, "Rekha tai", at=SUNRISE)
    big = await tiffin(db_session, SUNITA, lunch, 4)
    held = await assign(db_session, rekha, lunch, {big: 4})
    await two_wheeler(db_session, tiffins=3)

    await solve(db_session)

    plan = await the_plan(db_session)
    assert plan.status == "solved" and plan.summary is not None
    assert plan.summary["problems"] == [
        f"kitchen {held}: 4 portions, a two-wheeler carries 3",
        "TW-1 trip 1: 4 portions on board",
    ]
    [review] = await events(db_session, "logistics.plan_needs_review")
    assert (review["kind"], review["plan_id"]) == ("tiffin", str(plan.id))
    [stops] = await stops_by_route(db_session, plan.id)
    assert [s.kind for s in stops] == ["kitchen", "drop"]
    assert stops[1].society_id == GREEN_VALLEY and stops[1].rider_mode  # no captain there


@time_machine.travel(SOLVE_AT, tick=False)
async def test_no_two_wheeler_fails_and_a_day_without_tiffins_publishes_an_empty_plan(
    db_session: AsyncSession,
) -> None:
    await solve(db_session)
    empty = await the_plan(db_session)
    assert (empty.status, empty.summary and empty.summary["routes"]) == ("published", 0)

    lunch = await lunch_slot(db_session)
    lata = await kitchen(db_session, LATA, "Lata tai", at=SUNRISE)
    await assign(db_session, lata, lunch, {await tiffin(db_session, PRIYA, lunch, 1): 1})
    await vehicle(db_session, "EV-1")  # a van carries no tiffins
    await db_session.execute(
        update(RoutePlan).where(RoutePlan.id == empty.id).values(status="failed")
    )
    await db_session.commit()

    await solve(db_session)

    failed = await the_plan(db_session)
    assert (failed.status, failed.error) == ("failed", "no active two_wheeler vehicle at the hub")


@time_machine.travel(SOLVE_AT, tick=False)
async def test_a_kitchen_without_a_location_leaves_its_tiffins_unserved_and_never_publishes(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    lunch = await lunch_slot(db_session)
    lata = await kitchen(db_session, LATA, "Lata tai", at=SUNRISE)
    priya = await tiffin(db_session, PRIYA, lunch, 2)
    held = await assign(db_session, lata, lunch, {priya: 2})
    await db_session.execute(
        update(Kitchen).where(Kitchen.partner_id == lata).values(location=None)
    )
    await two_wheeler(db_session)
    await db_session.commit()

    await solve(db_session)

    plan = await the_plan(db_session)
    assert plan.status == "solved" and plan.summary is not None
    assert plan.summary["problems"] == [f"kitchen {held}: no location"]
    assert plan.unserved == [str(priya)]
    api = clients()
    manager = await as_user(api, google, VIKRAM)
    refused = await api.post(
        f"/v1/route-plans/{plan.id}/publish",
        json={"accept_problems": True},
        headers={**manager, **key()},
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "logistics.plan_incomplete"
    assert refused.json()["order_ids"] == [str(priya)]


@time_machine.travel(SOLVE_AT, tick=False)
async def test_an_empty_kitchen_is_skipped_and_a_missing_ready_by_is_its_own_problem(
    db_session: AsyncSession,
) -> None:
    lunch = await lunch_slot(db_session)
    lata = await kitchen(db_session, LATA, "Lata tai", at=SUNRISE)
    priya = await tiffin(db_session, PRIYA, lunch, 2)
    held = await assign(db_session, lata, lunch, {priya: 2})
    [ref] = await partners_api.tiffin_pickups(db_session, SEED_HUB_ID, T, "lunch")

    # No orders left: no location needed, nothing to report, nothing unserved.
    empty = dataclasses.replace(ref, orders=(), order_ids=(), lat=None, lng=None, ready_by=None)
    kitchens, _, _, problems, unserved = await tiffin_plans._kitchens(db_session, [empty])
    assert (kitchens, problems, unserved) == ([], [], [])

    # A kitchen with orders: "no location" and "no ready-by time" are told apart.
    for change, text in (({"lat": None}, "no location"), ({"ready_by": None}, "no ready-by time")):
        broken = dataclasses.replace(ref, **change)
        kitchens, _, _, problems, unserved = await tiffin_plans._kitchens(db_session, [broken])
        assert (kitchens, problems, unserved) == ([], [f"kitchen {held}: {text}"], [str(priya)])


@time_machine.travel(SOLVE_AT, tick=False)
async def test_a_manual_tiffin_solve_is_queued_solved_and_never_published_by_itself(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    lunch = await lunch_slot(db_session)
    lata = await kitchen(db_session, LATA, "Lata tai", at=SUNRISE)
    await assign(db_session, lata, lunch, {await tiffin(db_session, PRIYA, lunch, 1): 1})
    await two_wheeler(db_session)
    api = clients()
    manager = await as_user(api, google, VIKRAM)

    asked = await api.post(
        f"/v1/hubs/{SEED_HUB_ID}/route-plans",
        json={"date": T.isoformat(), "kind": "tiffin"},
        headers={**manager, **key()},
    )

    assert asked.status_code == 202, asked.text
    assert (asked.json()["kind"], asked.json()["status"]) == ("tiffin", "queued")
    ran = await solve(db_session)  # the job also solves manual plans still queued
    assert ran["plans"] == 1 and ran["skipped"] == 1  # the hub's queued plan is in flight
    plan = await db_session.get(RoutePlan, uuid.UUID(asked.json()["id"]), populate_existing=True)
    assert plan is not None and (plan.status, plan.trigger) == ("solved", "manual")
    published = await api.post(
        f"/v1/route-plans/{plan.id}/publish", json={}, headers={**manager, **key()}
    )
    assert published.status_code == 200, published.text
    assert published.json()["source"] == "nearest"


def place(kind: str, name: str, *tiffins: tiffin_plans.Tiffin) -> tiffin_plans.Place:
    return tiffin_plans.Place(name, kind, SUNRISE, (41_400, 43_200), tuple(tiffins))


async def test_the_check_catches_a_tiffin_on_no_drop_or_before_its_kitchen() -> None:
    society, slot = uuid.uuid4(), uuid.uuid4()
    a, b = (tiffin_plans.Tiffin(uuid.uuid4(), f"T-0{n}", 1, society, slot) for n in (1, 2))
    kitchen_a = place("kitchen", "kitchen/a", a, b)
    places = {
        "kitchen/a": kitchen_a,
        "drop/1": place("drop", "drop/1", a),
        "drop/2": place("drop", "drop/2", a),
    }
    van = Vehicle(label="TW-1", capacity_tiffins=30)
    early = Trip(0, 40_000, ["drop/1", "kitchen/a", "drop/2"], end_s=44_000)

    problems = tiffin_plans.check([early], places, [kitchen_a], [van], 50_000)

    assert problems == ["tiffin T-01: on 2 drops", "tiffin T-02: on 0 drops"]
    late = Trip(0, 40_000, ["drop/1", "kitchen/a"], end_s=44_000)
    assert tiffin_plans.check([late], places, [kitchen_a], [van], 50_000) == [
        "tiffin T-01: not after its kitchen",
        "tiffin T-02: on 0 drops",
    ]


async def test_m8_gives_m7_the_kitchens_tiffins_with_their_labels_and_places(
    db_session: AsyncSession,
) -> None:
    lunch = await lunch_slot(db_session)
    lata = await kitchen(db_session, LATA, "Lata tai", at=SUNRISE)
    priya = await tiffin(db_session, PRIYA, lunch, 2)
    held = await assign(db_session, lata, lunch, {priya: 2})
    await db_session.execute(
        update(Kitchen).where(Kitchen.partner_id == lata).values(area="Sector 20, Kharghar")
    )
    await db_session.commit()

    [ref] = await partners_api.tiffin_pickups(db_session, SEED_HUB_ID, T, "lunch")

    assert (ref.assignment_id, ref.slot_id, ref.order_ids) == (held, lunch, (priya,))
    [order] = ref.orders
    assert (order.order_id, order.portions, order.short_code.startswith("T-")) == (
        priya,
        2,
        True,
    )
    places = await partners_api.kitchen_places(db_session, [lata, uuid.uuid4()])
    assert list(places) == [lata]
    assert (places[lata].display_name, places[lata].area) == ("Lata tai", "Sector 20, Kharghar")
    assert places[lata].lat == pytest.approx(SUNRISE[0])
    assert datetime.now(UTC)
