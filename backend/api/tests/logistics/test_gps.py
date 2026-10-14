"""GPS (M7 §3.3, §6): riders send pings only on a started route, bad ones are dropped, the rate
is limited, pings reach Postgres through Valkey (or at once without it); jobs close the day's
routes and purge old pings."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import time_machine
from redis.asyncio import Redis
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.logistics import tracking
from gharbasket.modules.logistics.models import GpsPing, Route
from gharbasket.modules.logistics.schemas import PingIn
from gharbasket.modules.orders.models import Order, OrderEvent
from gharbasket.platform.jobs import run_job

from ..conftest import make_settings
from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .conftest import (
    ARJUN,
    KAVITA,
    MORNING,
    Day,
    as_user,
    key,
    pack,
    published_day,
    user_id,
)

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("world")]


def ping(seconds_ago: float, **change: Any) -> dict[str, Any]:
    at = MORNING - timedelta(seconds=seconds_ago)
    return {"at": at.isoformat(), "lat": 19.0331, "lng": 73.0298, "accuracy_m": 12, **change}


async def started(api: Any, google: FakeGoogle, db: AsyncSession) -> tuple[Day, dict[str, str]]:
    day = await published_day(db)
    await pack(db, *day.orders.values())
    rider = await as_user(api, google, ARJUN)
    answer = await api.post(f"/v1/routes/{day.route_id}/start", headers={**rider, **key()})
    assert answer.status_code == 200, answer.text
    return day, rider


async def pings_in_db(db: AsyncSession) -> int:
    return int(await db.scalar(select(func.count()).select_from(GpsPing)) or 0)


@time_machine.travel(MORNING, tick=False)
async def test_pings_go_through_valkey_into_postgres_and_bad_ones_are_dropped(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, valkey: Redis
) -> None:
    api = clients(valkey=valkey)
    day = await published_day(db_session)
    rider = await as_user(api, google, ARJUN)
    body = {"route_id": str(day.route_id), "pings": [ping(30)]}
    early = await api.post("/v1/riders/me/location", json=body, headers=rider)
    assert (early.status_code, early.json()["code"]) == (409, "logistics.no_active_route")
    await pack(db_session, *day.orders.values())
    assert (
        await api.post(f"/v1/routes/{day.route_id}/start", headers={**rider, **key()})
    ).status_code == 200

    batch = [
        ping(700),  # more than 10 minutes old
        ping(30),
        ping(28),  # 2 s after the previous one
        ping(20, lat=1.0),  # outside India
        ping(15, accuracy_m=500),
        ping(10, heading=90, speed_mps=6.1),
        ping(-120),  # 2 minutes ahead of the clock
    ]
    sent = await api.post("/v1/riders/me/location", json={**body, "pings": batch}, headers=rider)
    assert sent.status_code == 202, sent.text
    assert sent.json() == {"accepted": 2, "dropped": 5}
    replay = await api.post("/v1/riders/me/location", json={**body, "pings": batch}, headers=rider)
    assert replay.json() == {"accepted": 0, "dropped": 7}
    latest = json.loads(await valkey.get(tracking.loc_key(day.route_id)) or "{}")
    assert (latest["heading"], latest["lat"]) == (90, 19.0331)
    assert await valkey.ttl(tracking.loc_key(day.route_id)) > 800
    assert await pings_in_db(db_session) == 0

    run = await run_job(
        db_session,
        "persist_gps_pings",
        settings=make_settings(),
        scheduled_for=MORNING,
        valkey=valkey,
    )
    assert run.summary == {"read": 2, "inserted": 2}
    assert await valkey.xlen(tracking.STREAM) == 0
    assert await pings_in_db(db_session) == 2
    again = await run_job(
        db_session,
        "persist_gps_pings",
        settings=make_settings(),
        scheduled_for=MORNING,
        valkey=valkey,
    )
    assert again.summary == {"read": 0, "inserted": 0}

    # Someone else's route, and nobody but riders.
    await db_session.execute(
        update(Route).where(Route.id == day.route_id).values(rider_user_id=uuid.uuid4())
    )
    await db_session.commit()
    other = await api.post("/v1/riders/me/location", json=body, headers=rider)
    assert other.json()["code"] == "logistics.no_active_route"
    packer = await as_user(api, google, KAVITA)
    assert (await api.post("/v1/riders/me/location", json=body, headers=packer)).status_code == 403


@time_machine.travel(MORNING, tick=False)
async def test_without_valkey_pings_go_to_postgres_at_once_and_the_latest_position_comes_from_there(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    day, rider = await started(api, google, db_session)
    body = {"route_id": str(day.route_id), "pings": [ping(20), ping(10, heading=45)]}
    sent = await api.post("/v1/riders/me/location", json=body, headers=rider)
    assert sent.json() == {"accepted": 2, "dropped": 0}
    assert await pings_in_db(db_session) == 2
    assert (await api.post("/v1/riders/me/location", json=body, headers=rider)).json() == {
        "accepted": 0,
        "dropped": 2,
    }
    position = await tracking.latest(db_session, None, day.route_id)
    assert position is not None
    assert (position.at, position.heading, round(position.lat, 4)) == (
        MORNING - timedelta(seconds=10),
        45,
        19.0331,
    )
    # A replayed batch written straight to Postgres inserts nothing either.
    rows = [
        {
            "rider_user_id": await user_id(db_session, ARJUN),
            "route_id": day.route_id,
            "at": MORNING - timedelta(seconds=10),
            "location": "SRID=4326;POINT(73.03 19.03)",
            "accuracy_m": 5,
        }
    ]
    assert await tracking.insert_pings(db_session, rows) == 0


@time_machine.travel(MORNING, tick=False)
async def test_location_is_rate_limited_per_rider(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, valkey: Redis
) -> None:
    api = clients(valkey=valkey)
    day, rider = await started(api, google, db_session)
    body = {"route_id": str(day.route_id), "pings": [ping(10)]}
    for _ in range(12):
        assert (
            await api.post("/v1/riders/me/location", json=body, headers=rider)
        ).status_code == 202
    limited = await api.post("/v1/riders/me/location", json=body, headers=rider)
    assert (limited.status_code, limited.json()["code"]) == (429, "logistics.location_rate_limited")
    assert int(limited.headers["Retry-After"]) > 0


async def test_close_routes_sends_the_days_open_routes_bags_back_and_purge_drops_old_pings(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, valkey: Redis
) -> None:
    with time_machine.travel(MORNING, tick=False):
        api = clients(valkey=valkey)
        day, _ = await started(api, google, db_session)
        await valkey.set(tracking.loc_key(day.route_id), "{}")
    evening = MORNING.replace(hour=10, minute=30)  # 16:00 IST
    run = await run_job(
        db_session, "close_routes", settings=make_settings(), scheduled_for=evening, valkey=valkey
    )
    assert run.summary == {"date": "2026-10-13", "closed": 1, "returned_to_hub": 4}
    route = await db_session.get(Route, day.route_id, populate_existing=True)
    assert route is not None and route.status == "completed"
    assert await valkey.get(tracking.loc_key(day.route_id)) is None
    moves = await db_session.execute(
        select(OrderEvent.actor, OrderEvent.payload).where(OrderEvent.to_status == "undelivered")
    )
    assert {(a, (p or {})["reason"]) for a, p in moves.all()} == {("system", "returned_to_hub")}
    statuses = await db_session.execute(
        select(Order.status).where(Order.id.in_(day.orders.values()))
    )
    assert set(statuses.scalars()) == {"undelivered"}

    arjun = await user_id(db_session, ARJUN)
    old = datetime(2026, 6, 20, 8, 0, tzinfo=UTC)
    recent = datetime(2026, 10, 1, 8, 0, tzinfo=UTC)
    good = PingIn(at=old, lat=19.03, lng=73.03, accuracy_m=5)
    rows = [
        tracking._row(arjun, day.route_id, good),  # pyright: ignore[reportPrivateUsage]
        tracking._row(arjun, day.route_id, good.model_copy(update={"at": recent})),  # pyright: ignore[reportPrivateUsage]
    ]
    assert await tracking.insert_pings(db_session, rows) == 2
    await db_session.commit()
    purge = await run_job(
        db_session, "purge_gps_pings", settings=make_settings(), scheduled_for=evening
    )
    assert purge.summary == {"deleted": 1, "partitions_dropped": 1}
    assert await pings_in_db(db_session) == 1
    tables = await db_session.execute(
        text("SELECT tablename FROM pg_tables WHERE tablename LIKE 'gps_pings_%' ORDER BY 1")
    )
    assert list(tables.scalars()) == ["gps_pings_202610"]
