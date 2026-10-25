"""logistics.api.route_facts: M12's route rows (M12 §3.1)."""

import uuid
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.logistics import api as logistics_api
from gharbasket.modules.logistics.facts import share
from gharbasket.modules.logistics.models import Route, RoutePlan, RouteStop, Vehicle
from gharbasket.tools.seed import SEED_HUB_ID, seed_hubs

pytestmark = [pytest.mark.anyio, pytest.mark.services]

IST = ZoneInfo("Asia/Kolkata")
TUESDAY = date(2026, 11, 3)


def test_a_cost_is_shared_by_km_to_the_paisa() -> None:
    assert share(1_000, [10.0, 30.0]) == [250, 750]
    assert share(100, [1.0, 1.0, 1.0]) == [34, 33, 33]
    assert share(7, [0.0, 0.0]) == [4, 3]
    assert share(5, []) == []


async def test_route_facts_count_stops_on_time_and_share_the_plan_cost(
    db_session: AsyncSession,
) -> None:
    await seed_hubs(db_session)
    van = Vehicle(
        hub_id=SEED_HUB_ID,
        label="EV-9",
        kind="cargo_3w",
        capacity_kg=500,
        capacity_crates=20,
        capacity_tiffins=0,
    )
    published = RoutePlan(
        hub_id=SEED_HUB_ID,
        delivery_date=TUESDAY,
        kind="box",
        status="published",
        trigger="job",
        source="fallback_nearest",
        summary={"cost_paise": 1_000},
    )
    old = RoutePlan(
        hub_id=SEED_HUB_ID, delivery_date=TUESDAY, kind="box", status="superseded", trigger="job"
    )
    db_session.add_all([van, published, old])
    await db_session.flush()

    def route(plan: RoutePlan, trip: int, km: float, status: str = "completed") -> Route:
        return Route(
            plan_id=plan.id,
            hub_id=SEED_HUB_ID,
            delivery_date=TUESDAY,
            kind="box",
            trip=trip,
            vehicle_id=van.id,
            status=status,
            depart_time=time(6, 30),
            return_by=time(10),
            planned_km=km,
        )

    first, second = route(published, 1, 10.0), route(published, 2, 30.0, "in_progress")
    db_session.add_all([first, second, route(old, 1, 5.0)])
    await db_session.flush()

    def stop(r: Route, seq: int, orders: int, handed: time | None) -> RouteStop:
        return RouteStop(
            route_id=r.id,
            seq=seq,
            society_id=uuid.uuid4(),
            window_start=time(7),
            window_end=time(9),
            eta=time(8),
            orders=orders,
            handed_over_at=None if handed is None else datetime.combine(TUESDAY, handed, IST),
        )

    db_session.add_all(
        [
            stop(first, 1, 10, time(8, 0)),
            stop(first, 2, 12, time(9, 0)),  # by the window's end: on time
            stop(first, 3, 5, time(9, 20)),  # late
            stop(second, 1, 8, None),  # not handed over yet
        ]
    )
    await db_session.flush()

    facts = await logistics_api.route_facts(db_session, date_from=TUESDAY, date_to=TUESDAY)

    by_id = {f.route_id: f for f in facts}
    assert set(by_id) == {first.id, second.id}
    one, two = by_id[first.id], by_id[second.id]
    assert (one.km_m, one.cost_paise, one.orders, one.stops) == (10_000, 250, 27, 3)
    assert (one.on_time_stops, one.late_stops, one.source) == (2, 1, "fallback_nearest")
    assert (two.km_m, two.cost_paise, two.orders, two.stops) == (30_000, 750, 8, 1)
    assert (two.on_time_stops, two.late_stops, two.status) == (0, 0, "in_progress")
    assert {f.data_source for f in facts} == {"synthetic"}  # the seed hub's
    assert (
        await logistics_api.route_facts(
            db_session, date_from=TUESDAY, date_to=TUESDAY, hub_ids=[uuid.uuid4()]
        )
        == []
    )

    published.summary = {}
    await db_session.flush()
    facts = await logistics_api.route_facts(db_session, date_from=TUESDAY, date_to=TUESDAY)
    assert {f.cost_paise for f in facts} == {None}
