"""Live tracking (M7 §3.3): the SSE stream shows each audience its part, snapshot first, and
ends at the viewer's handover, at the route's end or for a reconnect; 3 streams per user."""

import json
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import pytest
import time_machine
from redis.asyncio import Redis
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.logistics import live, tracking
from gharbasket.modules.logistics.models import Route, RouteStop
from gharbasket.modules.logistics.schemas import PingIn
from gharbasket.platform.errors import ProblemError
from gharbasket.tools.seed import SEED_SOCIETY_ID

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .conftest import (
    ARJUN,
    KAVITA,
    MEENA,
    MORNING,
    PRIYA,
    RAMESH,
    VIKRAM,
    Day,
    as_user,
    key,
    pack,
    published_day,
    user_id,
)

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("world")]

SAVITA = "+919800001012"  # Krishna Kunj's captain
GREEN_VALLEY = uuid.UUID("0199a000-0000-7000-8000-00000000b002")

SSE = {"Accept": "text/event-stream"}


def parse(body: str) -> list[dict[str, Any]]:
    frames = []
    for block in body.split("\n\n"):
        if not block:
            continue
        frame: dict[str, Any] = {}
        for line in block.split("\n"):
            field, _, value = line.partition(": ")
            if field == "data":
                frame["data"] = json.loads(value)
            elif field:
                frame[field] = value
            else:
                frame["comment"] = value
        frames.append(frame)
    return frames


async def started(api: Any, google: FakeGoogle, db: AsyncSession) -> tuple[Day, dict[str, str]]:
    day = await published_day(db)
    await pack(db, *day.orders.values())
    rider = await as_user(api, google, ARJUN)
    answer = await api.post(f"/v1/routes/{day.route_id}/start", headers={**rider, **key()})
    assert answer.status_code == 200, answer.text
    return day, rider


@time_machine.travel(MORNING, tick=False)
async def test_the_stream_shows_each_audience_its_part_and_ends_at_the_handover(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, valkey: Redis
) -> None:
    api = clients(valkey=valkey)
    day, rider = await started(api, google, db_session)
    path = f"/v1/routes/{day.route_id}/live"
    sunrise = await db_session.scalar(
        select(RouteStop).where(
            RouteStop.route_id == day.route_id, RouteStop.society_id == SEED_SOCIETY_ID
        )
    )
    assert sunrise is not None
    sunrise_id, sunrise_seq = sunrise.id, sunrise.seq
    await valkey.set(
        tracking.loc_key(day.route_id),
        json.dumps({"at": MORNING.isoformat(), "lat": 19.033123, "lng": 73.029876, "heading": 7}),
    )

    # A captain and a customer of Sunrise Towers: one stop, the stops before it, the van rounded
    # for the customer, until the stop is handed over; then `stop`, `end` and no more positions.
    for customer, digits in ((False, 6), (True, 4)):
        await db_session.execute(
            update(RouteStop).where(RouteStop.id == sunrise_id).values(status="pending")
        )
        await db_session.commit()

        async def hand_over() -> None:
            await db_session.execute(
                update(RouteStop).where(RouteStop.id == sunrise_id).values(status="handed_over")
            )
            await db_session.commit()

        steps = [hand_over]

        async def sleep(_: float, steps: list[Any] = steps) -> None:
            if steps:
                await steps.pop(0)()

        body = live.stream(
            db_session,
            valkey,
            day.route_id,
            live.Audience(sunrise_id, customer=customer),
            sleep=sleep,
            clock=lambda: 0.0,
        )
        frames = parse(b"".join([chunk async for chunk in body]).decode())
        assert [f["event"] for f in frames] == ["snapshot", "stop", "end"]
        snapshot = frames[0]["data"]
        assert [s["seq"] for s in snapshot["stops"]] == [sunrise_seq]
        assert snapshot["stops_before"] == sunrise_seq - 1
        assert snapshot["position"]["lat"] == round(19.033123, digits)
        assert frames[1]["data"]["status"] == "handed_over"
        assert frames[2]["data"] == {"reason": "handed_over"}

    # Through the API, once the stop is handed over: the snapshot carries no position.
    for phone in (MEENA, PRIYA):
        who = await as_user(api, google, phone)
        answer = await api.get(path, headers={**who, **SSE})
        assert answer.status_code == 200, answer.text
        assert answer.headers["x-accel-buffering"] == "no"
        snapshot, end = parse(answer.text)
        assert snapshot["data"]["position"] is None
        assert end == {"event": "end", "data": {"reason": "handed_over"}}

    stranger = await as_user(api, google, KAVITA)
    assert (await api.get(path, headers={**stranger, **SSE})).status_code == 403
    missing = await api.get(f"/v1/routes/{uuid.uuid4()}/live", headers={**rider, **SSE})
    assert (missing.status_code, missing.json()["code"]) == (404, "logistics.route_not_found")

    # The rider and the hub manager see every stop; a completed route ends the stream.
    await db_session.execute(
        update(Route).where(Route.id == day.route_id).values(status="completed")
    )
    await db_session.commit()
    for who in (rider, await as_user(api, google, VIKRAM)):
        frames = parse((await api.get(path, headers={**who, **SSE})).text)
        assert [f["event"] for f in frames] == ["snapshot", "end"]
        assert len(frames[0]["data"]["stops"]) == 3 and "stops_before" not in frames[0]["data"]
        assert frames[1]["data"] == {"reason": "route_completed"}

    # At most 3 open streams per user.
    arjun = await user_id(db_session, ARJUN)
    for _ in range(3):
        assert await live.claim(valkey, arjun)
    full = await api.get(path, headers={**rider, **SSE})
    assert (full.status_code, full.json()["code"]) == (429, "logistics.too_many_streams")


@time_machine.travel(MORNING, tick=False)
async def test_a_running_stream_sends_positions_stops_keep_alives_and_reconnects(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, valkey: Redis
) -> None:
    api = clients(valkey=valkey)
    day, _ = await started(api, google, db_session)
    now = [0.0]
    steps: list[Any] = []

    async def sleep(seconds: float) -> None:
        now[0] += seconds
        if steps:
            await steps.pop(0)()

    async def move() -> None:
        await valkey.set(
            tracking.loc_key(day.route_id),
            json.dumps({"at": MORNING.isoformat(), "lat": 19.1, "lng": 73.1, "heading": None}),
        )
        await db_session.execute(
            update(RouteStop).where(RouteStop.id == day.stop_ids[0]).values(status="arrived")
        )
        await db_session.commit()

    async def idle() -> None:
        now[0] += live.PING_S

    async def late() -> None:
        now[0] += live.MAX_S

    steps.extend([move, idle, late])
    body: AsyncIterator[bytes] = live.stream(
        db_session, valkey, day.route_id, live.Audience(None), sleep=sleep, clock=lambda: now[0]
    )
    frames = parse(b"".join([chunk async for chunk in body]).decode())
    assert [f.get("event", f.get("comment")) for f in frames] == [
        "snapshot",
        "stop",
        "position",
        "keep-alive",
        "keep-alive",
        "end",
    ]
    assert frames[0]["data"]["position"] is None
    assert frames[1]["data"]["status"] == "arrived"
    assert frames[2]["data"] == {
        "at": MORNING.isoformat(),
        "lat": 19.1,
        "lng": 73.1,
        "heading": None,
        "age_s": 0,
    }
    assert frames[-1]["data"] == {"reason": "reconnect"}


@time_machine.travel(MORNING, tick=False)
async def test_another_societys_captain_or_customer_is_refused(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    day, _ = await started(api, google, db_session)
    # Krishna Kunj leaves the route: its captain (Savita, who lives there with no order today)
    # and its customer (Ramesh) have nothing to watch on it.
    await db_session.execute(
        delete(RouteStop).where(
            RouteStop.route_id == day.route_id,
            RouteStop.society_id.not_in([SEED_SOCIETY_ID, GREEN_VALLEY]),
        )
    )
    await db_session.commit()
    for phone in (SAVITA, RAMESH):
        who = await as_user(api, google, phone)
        refused = await api.get(f"/v1/routes/{day.route_id}/live", headers={**who, **SSE})
        assert (refused.status_code, refused.json()["code"]) == (403, "auth.forbidden")


@time_machine.travel(MORNING, tick=False)
async def test_without_valkey_the_stream_reads_the_latest_persisted_ping_every_15_s(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    day, _ = await started(api, google, db_session)
    arjun = await user_id(db_session, ARJUN)
    seen = PingIn(at=MORNING - timedelta(seconds=30), lat=19.05, lng=73.01, accuracy_m=5)
    await tracking.insert_pings(db_session, [tracking._row(arjun, day.route_id, seen)])  # pyright: ignore[reportPrivateUsage]
    await db_session.commit()
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)
        await db_session.execute(
            update(Route).where(Route.id == day.route_id).values(status="completed")
        )
        await db_session.commit()

    body = live.stream(
        db_session, None, day.route_id, live.Audience(None), sleep=sleep, clock=lambda: 0.0
    )
    frames = parse(b"".join([chunk async for chunk in body]).decode())
    assert frames[0]["data"]["position"]["lat"] == 19.05
    assert frames[0]["data"]["position"]["age_s"] == 30
    assert frames[-1]["data"] == {"reason": "route_completed"}
    assert waits == [live.SLOW_POLL_S]


async def test_the_stream_limit_forgets_streams_past_their_30_minutes_and_releases_its_own(
    valkey: Redis,
) -> None:
    user = uuid.uuid4()
    key = f"gb:logistics:streams:{user}"
    # Three streams of a process that died an hour ago hold no slot.
    await valkey.zadd(key, {f"dead{i}": 1_000.0 + i for i in range(3)})
    first = await live.claim(valkey, user)
    assert first is not None and await valkey.zcard(key) == 1

    async def body() -> AsyncIterator[bytes]:
        yield b"x"

    chunks = [c async for c in live.counted(body(), valkey, user, first)]
    assert chunks == [b"x"] and await valkey.zcard(key) == 0  # released, never below zero
    ids = [await live.claim(valkey, user) for _ in range(3)]
    assert all(ids)
    with pytest.raises(ProblemError) as full:
        await live.claim(valkey, user)
    assert full.value.code == "logistics.too_many_streams"
    assert await valkey.zcard(key) == 3
