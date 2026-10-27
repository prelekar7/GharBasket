"""`logistics.api.tentative_day` (M10 §3.8): a published box plan's routes as published, else the
routes M7's own fallback would publish now (previous order, else nearest neighbour), over the
boxes M3 expects or over M4's orders; None without a box vehicle."""

import uuid
from datetime import date, time, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community.models import Society
from gharbasket.modules.logistics import api as logistics_api
from gharbasket.modules.logistics.models import Route, RouteStop
from gharbasket.platform.jobs import run_job
from gharbasket.tools.seed import SEED_HUB_ID, SEED_SOCIETY_ID

from ..conftest import make_settings
from .conftest import DAY, MEENA, PRIYA, RAMESH, SOLVED_AT, SUNITA, locked_order, slot, vehicle

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("world")]

KRISHNA_KUNJ = uuid.UUID("0199a000-0000-7000-8000-00000000b005")
GREEN_VALLEY = uuid.UUID("0199a000-0000-7000-8000-00000000b002")


async def published_trips(db: AsyncSession) -> list[list[str]]:
    rows = await db.execute(
        select(Route.id, RouteStop.society_id, RouteStop.slot_id)
        .join(RouteStop, RouteStop.route_id == Route.id)
        .where(Route.delivery_date == DAY)
        .order_by(Route.depart_time, Route.trip, Route.id, RouteStop.seq)
    )
    trips: dict[uuid.UUID, list[str]] = {}
    for route_id, society_id, slot_id in rows.all():
        trips.setdefault(route_id, []).append(f"{society_id}/{slot_id}")
    return list(trips.values())


async def test_without_a_box_vehicle_there_is_no_tentative_day(db_session: AsyncSession) -> None:
    assert await logistics_api.tentative_day(db_session, SEED_HUB_ID, DAY) is None
    await vehicle(db_session, "TW-1", kind="two_wheeler")
    assert await logistics_api.tentative_day(db_session, SEED_HUB_ID, DAY) is None
    assert await logistics_api.tentative_day(db_session, uuid.uuid4(), DAY) is None


async def test_the_fallbacks_routes_now_are_the_routes_it_publishes(
    db_session: AsyncSession,
) -> None:
    early, late = await slot(db_session, time(7)), await slot(db_session, time(9))
    for phone in (PRIYA, MEENA, RAMESH):
        await locked_order(db_session, phone, early, DAY)
    await locked_order(db_session, SUNITA, late, DAY)
    await vehicle(db_session, "EV-1", crates=1)  # one society a trip
    await vehicle(db_session, "EV-2", crates=1)

    before = await logistics_api.tentative_day(db_session, SEED_HUB_ID, DAY)
    run = await run_job(
        db_session, "solve_routes", settings=make_settings(), scheduled_for=SOLVED_AT
    )
    assert run.status == "ok" and run.summary and run.summary["published"] == 1
    after = await logistics_api.tentative_day(db_session, SEED_HUB_ID, DAY)

    assert before is not None and after is not None
    assert (before.routes_source, after.routes_source) == ("nearest", "published")
    assert before.trips is not None and after.trips is not None
    assert [t.stops for t in after.trips] == await published_trips(db_session)
    assert sorted((t.van, t.stops) for t in before.trips) == sorted(
        (t.van, t.stops) for t in after.trips
    )
    assert {s.id: (s.orders, s.crates, s.kg_tenths) for s in before.stops} == {
        s.id: (s.orders, s.crates, s.kg_tenths) for s in after.stops
    }
    assert [s.id for s in after.slots] == [str(early), str(late)]
    assert (after.vans.count, after.vans.capacity_crates, after.depart_s) == (2, 1, 6 * 3600 + 1800)
    # Back within 90 minutes of the latest window, the 09:00-11:00 slot's.
    assert after.return_by_s == 11 * 3600 + 90 * 60


async def test_expected_boxes_follow_the_previous_plan_or_the_nearest_neighbour(
    db_session: AsyncSession,
) -> None:
    early, late = await slot(db_session, time(7)), await slot(db_session, time(9))
    for phone in (PRIYA, RAMESH):
        await locked_order(db_session, phone, early, DAY)
    await vehicle(db_session, "EV-1", crates=1)  # one society a trip
    ev2 = await vehicle(db_session, "EV-2", crates=1)
    run = await run_job(
        db_session, "solve_routes", settings=make_settings(), scheduled_for=SOLVED_AT
    )
    assert run.status == "ok"
    # The published plan leaves for Krishna Kunj first: the next day's tentative routes keep it.
    await db_session.execute(
        update(Route).where(Route.vehicle_id == ev2.id).values(depart_time=time(6, 0))
    )
    await db_session.execute(
        update(Society).where(Society.id == GREEN_VALLEY).values(boundary=None)
    )
    expected = {
        (SEED_SOCIETY_ID, early): 3,
        (KRISHNA_KUNJ, early): 4,
        (GREEN_VALLEY, late): 2,  # no location: left out
        (SEED_SOCIETY_ID, uuid.uuid4()): 3,  # not one of the hub's box slots: left out
    }

    nxt = await logistics_api.tentative_day(
        db_session, SEED_HUB_ID, DAY + timedelta(days=1), expected=expected
    )
    later = await logistics_api.tentative_day(
        db_session, SEED_HUB_ID, DAY + timedelta(days=9), expected=expected
    )

    sunrise, krishna = f"{SEED_SOCIETY_ID}/{early}", f"{KRISHNA_KUNJ}/{early}"
    assert (
        nxt is not None and later is not None and nxt.trips is not None and later.trips is not None
    )
    assert {s.id: (s.orders, s.crates, s.kg_tenths, s.empties) for s in nxt.stops} == {
        sunrise: (3, 1, 240, 0),  # a crate for three boxes, 8 kg a box
        krishna: (4, 2, 320, 0),
    }
    assert nxt.routes_source == "previous"
    assert [(t.van, t.stops) for t in nxt.trips] == [(0, [krishna]), (1, [sunrise])]
    # More than 7 days after the published plan: nearest first (Sunrise Towers is nearer).
    assert later.routes_source == "nearest"
    assert [(t.van, t.stops) for t in later.trips] == [(0, [sunrise]), (1, [krishna])]


async def test_an_empty_day_still_has_the_hubs_slots_and_fleet(db_session: AsyncSession) -> None:
    early = await slot(db_session, time(7))
    await vehicle(db_session, "EV-1")
    day = await logistics_api.tentative_day(db_session, SEED_HUB_ID, date(2026, 11, 3), expected={})
    assert day is not None
    assert (day.stops, day.trips, [s.id for s in day.slots]) == ([], [], [str(early)])
    assert day.return_by_s == 9 * 3600 + 90 * 60
