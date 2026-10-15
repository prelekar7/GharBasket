"""Route plans (M7 §3.2): the 23:15 job solves tomorrow's boxes per hub through M10's solver, or
M7's fallback when the ML service does not answer, checks the plan, writes draft routes and
publishes clean plans; hub managers solve on demand and publish, superseding the day's plan."""

import json
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import httpx2
import pytest
import time_machine
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.catalog.models import Product
from gharbasket.modules.community.models import Society
from gharbasket.modules.hub_ops import service as hub_service
from gharbasket.modules.hub_ops.schemas import HubIn
from gharbasket.modules.logistics import jobs, plans, solver
from gharbasket.modules.logistics.models import Route, RoutePlan, RouteStop
from gharbasket.modules.orders.models import Order, OrderLine
from gharbasket.platform.geo import LatLng
from gharbasket.platform.jobs import run_job
from gharbasket.platform.models import AuditEntry, OutboxEvent
from gharbasket.platform.settings import Settings
from gharbasket.tools.seed import SEED_HUB_ID, SEED_SOCIETY_ID

from ..conftest import make_settings
from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from ..intel_gateway.test_plan_week import TOKEN, ml_transport
from .conftest import (
    ARJUN,
    KAVITA,
    MEENA,
    NEHA,
    PRIYA,
    RAMESH,
    SANJAY,
    SUNITA,
    VIKRAM,
    as_user,
    key,
    locked_order,
    slot,
    user_id,
    vehicle,
)

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("world")]

NOW = datetime(2026, 10, 12, 17, 45, tzinfo=UTC)  # Mon 12 Oct, 23:15 IST
DAY = date(2026, 10, 13)
KRISHNA_KUNJ = uuid.UUID("0199a000-0000-7000-8000-00000000b005")
GREEN_VALLEY = uuid.UUID("0199a000-0000-7000-8000-00000000b002")
ML = make_settings(ml_service_url="http://ml.test", ml_service_token=TOKEN)
PLANS = f"/v1/hubs/{SEED_HUB_ID}/route-plans"


def fake_ml(answer: Callable[[dict[str, Any]], dict[str, Any]], seen: list[dict[str, Any]]):
    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        seen.append(body)
        return httpx2.Response(200, json=answer(body))

    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


def ml_down() -> httpx2.AsyncClient:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("no answer within the gateway's 26 s", request=request)

    return httpx2.AsyncClient(transport=httpx2.MockTransport(handler))


async def solve_job(
    db: AsyncSession, http: httpx2.AsyncClient | None, settings: Settings = ML
) -> dict[str, Any]:
    run = await run_job(db, "solve_routes", settings=settings, scheduled_for=NOW, http=http)
    assert run.status == "ok", run.error
    assert run.summary is not None
    return run.summary


async def only_plan(db: AsyncSession, day: date = DAY) -> RoutePlan:
    found = list(
        (
            await db.execute(
                select(RoutePlan)
                .where(RoutePlan.delivery_date == day)
                .order_by(RoutePlan.id.desc())
                .execution_options(populate_existing=True)
            )
        ).scalars()
    )
    assert found
    return found[0]


async def events(db: AsyncSession, event_type: str) -> list[dict[str, Any]]:
    rows = await db.execute(
        select(OutboxEvent.payload)
        .where(OutboxEvent.event_type == event_type)
        .order_by(OutboxEvent.id)
    )
    return list(rows.scalars())


async def stops_of(
    db: AsyncSession, plan_id: uuid.UUID
) -> list[tuple[str, int, list[uuid.UUID | None]]]:
    """(vehicle id, trip, its societies in stop order) per route, by departure."""
    routes = (
        await db.execute(
            select(Route).where(Route.plan_id == plan_id).order_by(Route.depart_time, Route.trip)
        )
    ).scalars()
    out = []
    for route in routes:
        societies = (
            await db.execute(
                select(RouteStop.society_id)
                .where(RouteStop.route_id == route.id)
                .order_by(RouteStop.seq)
            )
        ).scalars()
        out.append((str(route.vehicle_id), route.trip, list(societies)))
    return out


async def day_of_orders(db: AsyncSession, day: date = DAY) -> dict[str, uuid.UUID]:
    """Sunrise Towers (Priya, Meena) and Krishna Kunj (Ramesh) at 07:00, Green Valley (Sunita)
    at 09:00; returns the slot ids."""
    early, late = await slot(db, time(7)), await slot(db, time(9))
    for phone in (PRIYA, MEENA, RAMESH):
        await locked_order(db, phone, early, day)
    await locked_order(db, SUNITA, late, day)
    return {"early": early, "late": late}


# --- the job with the ML service ------------------------------------------------------------


@time_machine.travel(NOW, tick=False)
async def test_the_job_sends_the_day_to_the_solver_and_publishes_its_clean_plan(
    db_session: AsyncSession,
) -> None:
    slots = await day_of_orders(db_session)
    # Eggs (6 pieces) without a unit weight count 0.5 kg a step.
    await db_session.execute(update(Product).where(Product.slug == "eggs").values(nutrition=None))
    await locked_order(db_session, PRIYA, slots["early"], DAY, {"eggs": (1, 4_200)})
    arjun = await user_id(db_session, ARJUN)
    ev2 = await vehicle(db_session, "EV-2", kg=300, crates=18)
    ev1 = await vehicle(db_session, "ev-1", rider=arjun)
    await vehicle(db_session, "Bike", kind="two_wheeler")  # not a box vehicle
    sunrise = f"{SEED_SOCIETY_ID}/{slots['early']}"
    krishna = f"{KRISHNA_KUNJ}/{slots['early']}"
    green = f"{GREEN_VALLEY}/{slots['late']}"

    def answer(body: dict[str, Any]) -> dict[str, Any]:
        stop = lambda i, eta: {"id": i, "kind": "drop", "eta_s": eta, "late_s": 0}  # noqa: E731
        return {
            "status": "solved",
            "source": "osrm",
            # van 0 is ev-1 (first by label): two trips; van 1 is EV-2
            "routes": [
                {"van": 0, "start_s": 34_000, "end_s": 38_000, "stops": [stop(green, 35_000)]},
                {"van": 1, "start_s": 24_000, "end_s": 29_000, "stops": [stop(krishna, 25_500)]},
                {"van": 0, "start_s": 24_100, "end_s": 28_000, "stops": [stop(sunrise, 25_200)]},
            ],
            "summary": {"vans": 2, "km": 31.4, "cost_paise": 80_000, "on_time": 1.0, "late_min": 0},
            "seconds": 1.2,
            "model": "cvrptw-waves-v0",
        }

    seen: list[dict[str, Any]] = []
    async with fake_ml(answer, seen) as http:
        summary = await solve_job(db_session, http)

    assert summary == {
        "delivery_date": "2026-10-13",
        "plans": 1,
        "published": 1,
        "solved": 0,
        "failed": 0,
        "skipped": 0,
        "abandoned": 0,
    }
    [sent] = seen
    # The golden request: the hub, 06:30 out, back by 11:00 + 90 min, the box fleet's smallest
    # capacities, one stop per society and slot with its orders, crates and weight.
    assert {k: sent[k] for k in ("hub", "depart_s", "return_by_s", "vans", "time_limit_s")} == {
        "hub": {"lat": 19.033, "lng": 73.0297},
        "depart_s": 23_400,
        "return_by_s": 39_600 + 5_400,
        "vans": {
            "count": 2,
            "capacity_kg": 300,
            "capacity_crates": 18,
            "traffic_factor": 1.5,
            "reload_s": 1200,
        },
        "time_limit_s": 20.0,
    }
    by_id = {s["id"]: s for s in sent["stops"]}
    assert {i: (s["orders"], s["crates"], s["kg_tenths"]) for i, s in by_id.items()} == {
        sunrise: (3, 1, 35 + 35 + 5),  # two boxes of 3.5 kg, and 6 eggs at 0.5 kg a step
        krishna: (1, 1, 35),
        green: (1, 1, 35),
    }
    assert (by_id[green]["window_start_s"], by_id[green]["window_end_s"]) == (32_400, 39_600)
    assert all(19.0 < s["lat"] < 19.05 and 73.0 < s["lng"] < 73.13 for s in sent["stops"])

    plan = await only_plan(db_session)
    assert (plan.status, plan.trigger, plan.source, plan.distance_source, plan.model) == (
        "published",
        "job",
        "ml",
        "osrm",
        "cvrptw-waves-v0",
    )
    assert plan.summary == {
        "stops": 3,
        "orders": 5,
        "vehicles": 2,
        "routes": 3,
        "km": 31.4,
        "cost_paise": 80_000,
        "on_time": 1.0,
        "late_min": 0.0,
        "problems": [],
    }
    assert plan.prediction_id is not None and plan.unserved == []
    # Van index → vehicle by label; a van's trips numbered by start time.
    assert await stops_of(db_session, plan.id) == [
        (str(ev2.id), 1, [KRISHNA_KUNJ]),
        (str(ev1.id), 1, [SEED_SOCIETY_ID]),
        (str(ev1.id), 2, [GREEN_VALLEY]),
    ]
    published = await events(db_session, "logistics.route_published")
    assert len(published) == 3
    first = next(e for e in published if e["vehicle_id"] == str(ev1.id) and e["trip"] == 1)
    meena = await user_id(db_session, MEENA)
    assert first == {
        "route_id": first["route_id"],
        "plan_id": str(plan.id),
        "hub_id": str(SEED_HUB_ID),
        "delivery_date": "2026-10-13",
        "kind": "box",
        "trip": 1,
        "vehicle_id": str(ev1.id),
        "rider_user_id": str(arjun),
        "previous_rider_user_id": None,
        "depart": "06:41",
        "stops": [
            {
                "seq": 1,
                "kind": "drop",
                "society_id": str(SEED_SOCIETY_ID),
                "slot_id": str(slots["early"]),
                "eta": "07:00",
                "bags": 3,
                "captain_user_id": str(meena),
            }
        ],
    }
    # Green Valley has no captain: the rider hands over and does the doors there.
    modes = dict(
        (await db_session.execute(select(RouteStop.society_id, RouteStop.rider_mode))).all()
    )
    assert modes == {SEED_SOCIETY_ID: False, KRISHNA_KUNJ: False, GREEN_VALLEY: True}

    # Idempotent per date: running again leaves the published plan alone.
    async with fake_ml(answer, seen) as http:
        again = await solve_job(db_session, http)
    assert (again["plans"], again["skipped"], len(seen)) == (0, 1, 1)


@time_machine.travel(NOW, tick=False)
async def test_with_the_real_solver_every_stop_is_served_within_capacity(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(solver, "TIME_LIMIT_S", 1.0)
    await day_of_orders(db_session)
    await vehicle(db_session, "EV-1")
    await vehicle(db_session, "EV-2")

    async with httpx2.AsyncClient(transport=ml_transport(), base_url="http://ml.test") as http:
        await solve_job(db_session, http)

    plan = await only_plan(db_session)
    assert (plan.status, plan.source, plan.distance_source) == ("published", "ml", "haversine")
    assert plan.summary is not None and plan.summary["problems"] == []
    served = [s for _, _, societies in await stops_of(db_session, plan.id) for s in societies]
    assert sorted(map(str, served)) == sorted(
        map(str, (SEED_SOCIETY_ID, KRISHNA_KUNJ, GREEN_VALLEY))
    )


# --- ML down: M7's fallback ------------------------------------------------------------------


@time_machine.travel(NOW, tick=False)
async def test_ml_down_falls_back_to_nearest_then_to_the_previous_plans_order(
    db_session: AsyncSession,
) -> None:
    await day_of_orders(db_session)
    ev1 = await vehicle(db_session, "EV-1", crates=1)  # one society a trip
    ev2 = await vehicle(db_session, "EV-2", crates=1)
    labels = {str(ev1.id): "EV-1", str(ev2.id): "EV-2"}

    async with ml_down() as http:
        await solve_job(db_session, http)

    plan = await only_plan(db_session)
    assert (plan.status, plan.source, plan.distance_source, plan.model) == (
        "published",
        "fallback_nearest",
        "haversine",
        None,
    )
    assert plan.summary is not None
    assert (plan.summary["problems"], plan.summary["routes"]) == ([], 3)
    trips = {(labels[v], n): s for v, n, s in await stops_of(db_session, plan.id)}
    # The 07:00 stops first, the nearest first (Sunrise Towers is nearer the hub than Krishna
    # Kunj); EV-1 is back first and takes the 09:00 stop.
    assert trips == {
        ("EV-1", 1): [SEED_SOCIETY_ID],
        ("EV-2", 1): [KRISHNA_KUNJ],
        ("EV-1", 2): [GREEN_VALLEY],
    }

    # The next day, with no ML service configured, the published plan's order is kept: make
    # Krishna Kunj its first stop to see it followed.
    await db_session.execute(
        update(Route)
        .where(Route.plan_id == plan.id, Route.vehicle_id == ev2.id)
        .values(depart_time=time(6, 0))
    )
    await day_of_orders(db_session, DAY + timedelta(days=1))
    run = await run_job(
        db_session, "solve_routes", settings=make_settings(), scheduled_for=NOW + timedelta(days=1)
    )
    assert run.status == "ok"
    later = await only_plan(db_session, DAY + timedelta(days=1))
    assert (later.status, later.source) == ("published", "fallback_previous")
    trips = {(labels[v], n): s for v, n, s in await stops_of(db_session, later.id)}
    assert trips[("EV-1", 1)] == [KRISHNA_KUNJ] and trips[("EV-2", 1)] == [SEED_SOCIETY_ID]


async def test_a_plan_with_problems_waits_for_review_and_an_unrouted_stop_never_publishes(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await day_of_orders(db_session)
    await vehicle(db_session, "EV-1")
    # Krishna Kunj loses its boundary: nowhere to drive to.
    await db_session.execute(
        update(Society).where(Society.id == KRISHNA_KUNJ).values(boundary=None)
    )
    await db_session.commit()

    await solve_job(db_session, None)

    plan = await only_plan(db_session)
    assert plan.status == "solved"
    assert plan.unserved and plan.unserved[0].startswith(f"{KRISHNA_KUNJ}/")
    assert plan.summary is not None
    assert plan.summary["problems"] == [f"{plan.unserved[0]}: the society has no location"]
    assert plan.summary["stops"] == 3 and plan.summary["routes"] >= 1
    unserved = list(plan.unserved)
    [review] = await events(db_session, "logistics.plan_needs_review")
    assert review == {
        "plan_id": str(plan.id),
        "hub_id": str(SEED_HUB_ID),
        "delivery_date": "2026-10-13",
        "kind": "box",
        "problems": plan.summary["problems"],
    }
    api = clients()
    manager = await as_user(api, google, VIKRAM)
    await db_session.commit()
    refused = await api.post(
        f"/v1/route-plans/{plan.id}/publish",
        json={"accept_problems": True},
        headers={**manager, **key()},
    )
    assert (refused.status_code, refused.json()["code"]) == (409, "logistics.plan_incomplete")
    assert refused.json()["stop_ids"] == unserved


@time_machine.travel(NOW, tick=False)
async def test_no_orders_publish_an_empty_plan_and_no_vehicle_fails_the_solve(
    db_session: AsyncSession,
) -> None:
    await solve_job(db_session, None)
    empty = await only_plan(db_session)
    assert (empty.status, empty.summary is not None and empty.summary["routes"]) == ("published", 0)

    await db_session.execute(delete(RoutePlan))
    await day_of_orders(db_session)
    summary = await solve_job(db_session, None)
    failed = await only_plan(db_session)
    assert summary["failed"] == 1
    assert (failed.status, failed.error) == ("failed", "no active cargo_3w vehicle at the hub")
    # A failed plan does not count: the job tries again (and fails again, with no vehicle).
    assert (await solve_job(db_session, None))["plans"] == 1


# --- on demand ----------------------------------------------------------------------------------


@time_machine.travel(NOW, tick=False)
async def test_a_manager_solves_on_demand_publishes_and_re_solves_until_a_route_starts(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await day_of_orders(db_session)
    await vehicle(db_session, "EV-1")
    api = clients()
    manager = await as_user(api, google, VIKRAM)
    vikram = await user_id(db_session, VIKRAM)
    await db_session.commit()
    body, headers = {"date": DAY.isoformat(), "kind": "box"}, {**manager, **key()}

    asked = await api.post(PLANS, json=body, headers=headers)
    replay = await api.post(PLANS, json=body, headers=headers)
    running = await api.post(PLANS, json=body, headers={**manager, **key()})

    assert asked.status_code == 202, asked.text
    queued = asked.json()
    assert replay.json() == queued
    assert (queued["status"], queued["trigger"], queued["summary"], queued["routes"]) == (
        "queued",
        "manual",
        None,
        [],
    )
    assert queued["requested_by"] == {"user_id": str(vikram), "name": "Vikram Desai"}
    assert (running.status_code, running.json()["code"]) == (409, "logistics.solve_running")
    not_yet = await api.post(
        f"/v1/route-plans/{queued['id']}/publish", headers={**manager, **key()}
    )
    assert (not_yet.status_code, not_yet.json()["code"]) == (409, "logistics.plan_not_publishable")

    # The background solve, as the API runs it after the request (here on the test's connection).
    maker = async_sessionmaker(bind=db_session.bind, join_transaction_mode="create_savepoint")
    await jobs.solve_queued(maker, None, make_settings(), uuid.UUID(queued["id"]))
    solved = (await api.get(f"/v1/route-plans/{queued['id']}", headers=manager)).json()
    assert (solved["status"], solved["source"]) == ("solved", "fallback_nearest")
    assert [r["status"] for r in solved["routes"]] == ["draft"]
    route = solved["routes"][0]
    assert (route["vehicle"]["label"], route["trip"], route["depart"], route["return_by"]) == (
        "EV-1",
        1,
        route["depart"],
        "12:30",
    )
    assert (route["stops_total"], route["stops_done"], route["bags"], route["rider"]) == (
        3,
        0,
        4,
        None,
    )

    published = await api.post(
        f"/v1/route-plans/{queued['id']}/publish", json={}, headers={**manager, **key()}
    )
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "published"
    assert [r["status"] for r in published.json()["routes"]] == ["published"]

    # Re-solve and publish: the first plan is superseded and its route cancelled.
    again = (await api.post(PLANS, json=body, headers={**manager, **key()})).json()
    await jobs.solve_queued(maker, None, make_settings(), uuid.UUID(again["id"]))
    swap = await api.post(f"/v1/route-plans/{again['id']}/publish", headers={**manager, **key()})
    assert swap.status_code == 200, swap.text
    old = (await api.get(f"/v1/route-plans/{queued['id']}", headers=manager)).json()
    assert (old["status"], [r["status"] for r in old["routes"]]) == ("superseded", ["cancelled"])
    [cancelled] = await events(db_session, "logistics.route_cancelled")
    assert cancelled == {
        "route_id": route["id"],
        "plan_id": queued["id"],
        "hub_id": str(SEED_HUB_ID),
        "delivery_date": "2026-10-13",
        "rider_user_id": None,
        "reason": "superseded",
    }
    listed = await api.get(PLANS, params={"date": DAY.isoformat()}, headers=manager)
    assert listed.headers["Cache-Control"] == "no-store"
    assert [p["id"] for p in listed.json()["items"]] == [again["id"], queued["id"]]
    assert "routes" not in listed.json()["items"][0]

    # Once a route of the published plan has started, neither a re-solve nor a publish replaces it.
    third = (await api.post(PLANS, json=body, headers={**manager, **key()})).json()
    await jobs.solve_queued(maker, None, make_settings(), uuid.UUID(third["id"]))
    await db_session.execute(
        update(Route).where(Route.plan_id == uuid.UUID(again["id"])).values(status="in_progress")
    )
    await db_session.commit()
    for refused in (
        await api.post(PLANS, json=body, headers={**manager, **key()}),
        await api.post(f"/v1/route-plans/{third['id']}/publish", headers={**manager, **key()}),
    ):
        assert (refused.status_code, refused.json()["code"]) == (409, "logistics.routes_started")


@time_machine.travel(NOW, tick=False)
async def test_publishing_with_problems_needs_accept_problems_and_is_audited(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    early = await slot(db_session, time(7))
    await locked_order(db_session, PRIYA, early, DAY, {"onion": (12, 4_000)})  # 12 kg
    await vehicle(db_session, "EV-1", kg=10)
    plan = await plans.request_solve(db_session, SEED_HUB_ID, DAY, "box", trigger="manual")
    await plans.run(db_session, plan, http=None, settings=make_settings())
    await db_session.commit()
    assert plan.summary is not None
    assert plan.summary["problems"] == ["EV-1 trip 1: over capacity leaving the hub"]
    problems, plan_id = plan.summary["problems"], plan.id
    api = clients()
    manager = await as_user(api, google, VIKRAM)
    await db_session.commit()
    url = f"/v1/route-plans/{plan_id}/publish"

    refused = await api.post(url, headers={**manager, **key()})
    assert (refused.status_code, refused.json()["code"]) == (409, "logistics.plan_has_problems")
    assert refused.json()["problems"] == problems
    accepted = await api.post(url, json={"accept_problems": True}, headers={**manager, **key()})
    assert accepted.status_code == 200, accepted.text
    entry = await db_session.scalar(
        select(AuditEntry).where(AuditEntry.action == "route_plans.published_with_problems")
    )
    assert entry is not None and entry.entity_id == str(plan_id)
    assert entry.after == {"problems": problems}


async def test_a_lost_solve_fails_and_an_unexpected_error_never_leaves_a_plan_in_flight(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    lost = await plans.request_solve(db_session, SEED_HUB_ID, DAY, "box", trigger="manual")
    await db_session.execute(
        update(RoutePlan)
        .where(RoutePlan.id == lost.id)
        .values(created_at=func.now() - timedelta(minutes=11))
    )
    fresh = await plans.request_solve(db_session, SEED_HUB_ID, DAY, "box", trigger="manual")
    await db_session.refresh(lost)
    assert (lost.status, lost.error) == ("failed", "abandoned: not solved within 10 minutes")

    async def boom(_: AsyncSession, plan: RoutePlan, **__: object) -> None:
        plan.source = "ml"  # changed, then lost with the savepoint
        await db_session.flush()
        raise RuntimeError("bug")

    monkeypatch.setattr(solver, "solve", boom)
    await plans.run(db_session, fresh, http=None, settings=make_settings())
    assert (fresh.status, fresh.error, fresh.source) == (
        "failed",
        "unexpected error: RuntimeError",
        None,
    )
    jobs.solve_soon(None, None, make_settings(), fresh.id)  # no pool: left to the job


# --- who may ------------------------------------------------------------------------------------


@time_machine.travel(NOW, tick=False)
async def test_plans_belong_to_the_hubs_managers_and_owners_only(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    plan = await plans.request_solve(db_session, SEED_HUB_ID, DAY, "box", trigger="manual")
    await db_session.commit()
    api = clients()
    manager = await as_user(api, google, VIKRAM)
    owner = await as_user(api, google, SANJAY)
    packer = await as_user(api, google, KAVITA)
    await db_session.commit()

    assert (await api.get(f"/v1/route-plans/{plan.id}", headers=owner)).status_code == 200
    assert (await api.get(PLANS, headers=owner)).status_code == 200
    for refused in (
        await api.post(PLANS, json={"date": DAY.isoformat()}, headers={**owner, **key()}),
        await api.get(PLANS, headers=packer),
        await api.get(f"/v1/hubs/{uuid.uuid4()}/route-plans", headers=manager),
    ):
        assert (refused.status_code, refused.json()["code"]) == (403, "auth.forbidden")
    unknown = await api.get(f"/v1/route-plans/{uuid.uuid4()}", headers=manager)
    assert (unknown.status_code, unknown.json()["code"]) == (404, "logistics.plan_not_found")
    for day in (DAY + timedelta(days=1), DAY - timedelta(days=2)):
        late = await api.post(PLANS, json={"date": day.isoformat()}, headers={**manager, **key()})
        assert (late.status_code, late.json()["code"]) == (422, "logistics.date_out_of_range")
    old = await api.get(PLANS, params={"date": "2026-10-04"}, headers=manager)
    assert old.json()["code"] == "logistics.date_out_of_range"
    tiffin = await api.post(
        PLANS, json={"date": DAY.isoformat(), "kind": "tiffin"}, headers={**manager, **key()}
    )
    assert tiffin.status_code == 422


# --- review fixes -------------------------------------------------------------------------------


async def test_a_pack_has_no_weight_of_its_own(db_session: AsyncSession) -> None:
    ids = await catalog_api.product_ids_by_slug(db_session, ["tomato"])
    tomato = ids["tomato"]
    weights = await catalog_api.line_grams(
        db_session, [(tomato, 2, 1, "pack"), (tomato, 2, 500, "g"), (tomato, 1, 1, "kg")]
    )
    assert weights == [None, 1000.0, 1000.0]


@time_machine.travel(NOW, tick=False)
async def test_packs_weigh_half_a_kilo_a_step_and_picking_orders_stay_on_the_plan(
    db_session: AsyncSession,
) -> None:
    early = await slot(db_session, time(7))
    priya = await locked_order(db_session, PRIYA, early, DAY)
    ramesh = await locked_order(db_session, RAMESH, early, DAY)
    # Priya's tomatoes sold by the pack (2 steps); Ramesh's box already being picked at 05:00.
    await db_session.execute(
        update(OrderLine)
        .where(OrderLine.order_id == priya, OrderLine.step_unit == "g", OrderLine.step_qty == 500)
        .values(step_unit="pack", step_qty=1)
    )
    await db_session.execute(update(Order).where(Order.id == ramesh).values(status="picking"))
    await vehicle(db_session, "EV-1")

    await solve_job(db_session, None)

    plan = await only_plan(db_session)
    assert plan.status == "published" and plan.summary is not None
    assert plan.summary["orders"] == 2
    kg = dict((await db_session.execute(select(RouteStop.society_id, RouteStop.kg_tenths))).all())
    # 3.5 kg less 1 kg of tomatoes plus 2 packs at 0.5 kg; Ramesh's box as it is.
    assert kg == {SEED_SOCIETY_ID: 35, KRISHNA_KUNJ: 35}


@time_machine.travel(NOW, tick=False)
async def test_an_odd_ml_answer_is_named_and_a_stop_on_two_routes_never_publishes(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    early = await slot(db_session, time(7))
    for phone in (PRIYA, RAMESH):
        await locked_order(db_session, phone, early, DAY)
    await vehicle(db_session, "EV-1")
    await vehicle(db_session, "EV-2")
    sunrise = f"{SEED_SOCIETY_ID}/{early}"
    krishna = f"{KRISHNA_KUNJ}/{early}"

    def answer(body: dict[str, Any]) -> dict[str, Any]:
        def stop(i: str, eta: int) -> dict[str, Any]:
            return {"id": i, "kind": "drop", "eta_s": eta, "late_s": 0}

        return {
            "status": "fallback",  # M10's own nearest neighbour
            "source": "haversine",
            "routes": [
                {
                    "van": 0,
                    "start_s": 24_000,
                    "end_s": 30_000,
                    "stops": [stop(sunrise, 25_200), stop(krishna, 26_500)],
                },
                {
                    "van": 1,
                    "start_s": 24_000,
                    "end_s": 30_000,
                    "stops": [stop(krishna, 26_000), stop("nowhere/at-all", 27_000)],
                },
            ],
            "summary": {"vans": 2, "km": 20.0, "cost_paise": 60_000, "on_time": 1.0, "late_min": 0},
            "seconds": 0.3,
            "model": "cvrptw-waves-v0",
        }

    async with fake_ml(answer, []) as http:
        await solve_job(db_session, http)

    plan = await only_plan(db_session)
    assert (plan.status, plan.source) == ("solved", "ml_fallback")
    assert plan.summary is not None
    assert plan.summary["problems"] == [
        "van 1: unknown stops nowhere/at-all",
        f"visited twice: {krishna}",
    ]
    plan_id = plan.id
    api = clients()
    manager = await as_user(api, google, VIKRAM)
    await db_session.commit()
    refused = await api.post(
        f"/v1/route-plans/{plan_id}/publish",
        json={"accept_problems": True},
        headers={**manager, **key()},
    )
    assert (refused.status_code, refused.json()["code"]) == (409, "logistics.plan_incomplete")
    assert refused.json()["stop_ids"] == [krishna]


@time_machine.travel(NOW, tick=False)
async def test_a_request_m10_would_refuse_is_planned_by_m7_alone(
    db_session: AsyncSession,
) -> None:
    await day_of_orders(db_session)
    for n in range(61):  # M10 takes at most 60 vans
        await vehicle(db_session, f"EV-{n:02d}")

    def never(body: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("an invalid request must not be sent")

    async with fake_ml(never, []) as http:
        await solve_job(db_session, http)

    plan = await only_plan(db_session)
    assert (plan.status, plan.source) == ("published", "fallback_nearest")


@time_machine.travel(NOW, tick=False)
async def test_one_hubs_error_never_undoes_another_hubs_plan(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = await hub_service.create_hub(
        db_session,
        HubIn(
            slug="vashi",
            name="Vashi hub",
            address="Sector 17, Vashi, Navi Mumbai 400703",
            location=LatLng(lat=19.0771, lng=72.9986),
        ),
    )
    await db_session.commit()
    real_run = plans.run

    async def run(session: AsyncSession, plan: RoutePlan, **kwargs: Any) -> None:
        if plan.hub_id == SEED_HUB_ID:
            plan.source = "ml"
            await session.flush()
            raise RuntimeError("bug")
        await real_run(session, plan, **kwargs)

    monkeypatch.setattr(plans, "run", run)
    summary = await solve_job(db_session, None)

    assert (summary["plans"], summary["published"], summary["failed"]) == (2, 1, 1)
    statuses = dict((await db_session.execute(select(RoutePlan.hub_id, RoutePlan.status))).all())
    assert statuses == {SEED_HUB_ID: "failed", other.id: "published"}


@time_machine.travel(NOW, tick=False)
async def test_publishing_one_solved_plan_supersedes_the_others(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    await day_of_orders(db_session)
    await vehicle(db_session, "EV-1")
    solved = []
    for _ in range(2):
        plan = await plans.request_solve(db_session, SEED_HUB_ID, DAY, "box", trigger="manual")
        await plans.run(db_session, plan, http=None, settings=make_settings())
        solved.append(plan.id)
    await db_session.commit()
    api = clients()
    manager = await as_user(api, google, VIKRAM)
    admin = await as_user(api, google, NEHA)
    await db_session.commit()

    published = await api.post(f"/v1/route-plans/{solved[0]}/publish", headers={**manager, **key()})
    assert published.status_code == 200, published.text
    newer = (await api.get(f"/v1/route-plans/{solved[1]}", headers=manager)).json()
    assert (newer["status"], [r["status"] for r in newer["routes"]]) == (
        "superseded",
        ["cancelled"],
    )
    late = await api.post(f"/v1/route-plans/{solved[1]}/publish", headers={**manager, **key()})
    assert (late.status_code, late.json()["code"]) == (409, "logistics.plan_not_publishable")
    unknown = await api.get(f"/v1/hubs/{uuid.uuid4()}/route-plans", headers=admin)
    assert (unknown.status_code, unknown.json()["code"]) == (404, "hub_ops.hub_not_found")
