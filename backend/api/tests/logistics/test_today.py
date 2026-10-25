"""logistics.api.today_routes: the routes, stops and delivery attempts of a hub's day (M12 §3.3's
today board)."""

import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.logistics import api as logistics_api
from gharbasket.modules.logistics.models import Delivery, Route, RoutePlan, RouteStop, Vehicle
from gharbasket.tools.seed import SEED_HUB_ID, seed_hubs

pytestmark = [pytest.mark.anyio, pytest.mark.services]

IST = ZoneInfo("Asia/Kolkata")
TUESDAY = date(2026, 11, 3)
NOW = datetime(2026, 11, 3, 8, 30, tzinfo=IST)  # the window of the 9:00 stops is still open


def _delivery(status: str, **values: object) -> Delivery:
    defaults: dict[str, object] = {
        "order_id": uuid.uuid4(),
        "attempt": 1,
        "hub_id": SEED_HUB_ID,
        "society_id": uuid.uuid4(),
        "order_kind": "box",
        "delivery_date": TUESDAY,
        "slot_id": uuid.uuid4(),
        "status": status,
        "delivery_code": "1234",
    }
    return Delivery(**(defaults | values))


async def test_today_routes_count_routes_stops_late_stops_and_attempts(
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

    def plan(status: str, day: date = TUESDAY, hub: uuid.UUID = SEED_HUB_ID) -> RoutePlan:
        return RoutePlan(hub_id=hub, delivery_date=day, kind="box", status=status, trigger="job")

    published, solved, superseded = plan("published"), plan("solved"), plan("superseded")
    tomorrow = plan("published", TUESDAY + timedelta(days=1))
    db_session.add_all([van, published, solved, superseded, tomorrow])
    await db_session.flush()

    def route(p: RoutePlan, trip: int, status: str, day: date = TUESDAY) -> Route:
        return Route(
            plan_id=p.id,
            hub_id=p.hub_id,
            delivery_date=day,
            kind="box",
            trip=trip,
            vehicle_id=van.id,
            status=status,
            depart_time=time(6, 30),
            return_by=time(10),
            planned_km=10.0,
        )

    done, running, waiting, cancelled = (
        route(published, 1, "completed"),
        route(published, 2, "in_progress"),
        route(published, 3, "published"),
        route(published, 4, "cancelled"),
    )
    db_session.add_all([done, running, waiting, cancelled, route(superseded, 1, "cancelled")])
    db_session.add(route(tomorrow, 1, "published", TUESDAY + timedelta(days=1)))
    await db_session.flush()

    def stop(
        r: Route, seq: int, status: str, handed: time | None, end: time = time(9)
    ) -> RouteStop:
        return RouteStop(
            route_id=r.id,
            seq=seq,
            society_id=uuid.uuid4(),
            window_start=time(7),
            window_end=end,
            eta=time(8),
            status=status,
            handed_over_at=None if handed is None else datetime.combine(TUESDAY, handed, IST),
        )

    db_session.add_all(
        [
            stop(done, 1, "handed_over", time(8, 0)),  # on time
            stop(done, 2, "handed_over", time(9, 20)),  # late
            stop(done, 3, "skipped", None),  # done, and never late
            stop(running, 1, "arrived", None),  # window open until 9:00: not late yet
            stop(running, 2, "pending", None, end=time(8)),  # window closed at 8:00: late
            stop(waiting, 1, "pending", None),
        ]
    )
    db_session.add_all(
        [
            _delivery("delivered"),
            _delivery("delivered"),
            _delivery("with_captain"),
            _delivery("held"),
            _delivery("on_route"),  # not counted anywhere
            _delivery("undelivered"),
            _delivery("redelivery"),
            _delivery("triage"),
            _delivery("delivered", delivery_date=TUESDAY + timedelta(days=1)),  # another day
            _delivery("held", hub_id=uuid.uuid4()),  # another hub
        ]
    )
    await db_session.flush()

    result = await logistics_api.today_routes(db_session, hub_id=SEED_HUB_ID, on=TUESDAY, now=NOW)

    assert result.planned == 3  # completed, in progress, published; not the cancelled one
    assert (result.started, result.completed) == (2, 1)
    assert (result.stops_total, result.stops_done, result.late_stops) == (6, 3, 2)
    assert (result.at_society, result.delivered, result.held, result.undelivered) == (1, 2, 1, 3)
    assert result.plans_to_review == 1
    assert result.oldest_plan_at == solved.created_at

    quiet = await logistics_api.today_routes(db_session, hub_id=uuid.uuid4(), on=TUESDAY, now=NOW)
    assert quiet == logistics_api.TodayRoutes(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, None)
