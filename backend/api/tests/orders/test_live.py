"""The live order timeline (M4 §3.3): JSON for polling clients, the SSE stream with snapshot,
status rows, pings, `end` and Last-Event-ID resumes, and the 3-streams-per-user limit."""

import json
import uuid
from typing import Any

import pytest
import time_machine
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.orders import live, service

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .test_issue_triage import staff
from .test_issues import DELIVERED, NOW, placed_order
from .test_orders import as_user

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("world")]

SSE = {"Accept": "text/event-stream"}
RAMESH = "+919800001003"


def parse(body: str) -> list[dict[str, Any]]:
    """SSE frames as {"event", "id", "data", "comment"}."""
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


async def rows(db: AsyncSession, order_id: uuid.UUID) -> list[dict[str, Any]]:
    return [live.event(r).model_dump(mode="json") for r in await service.timeline_of(db, order_id)]


@time_machine.travel(NOW, tick=False)
async def test_the_json_form_is_the_timeline_oldest_first_for_the_owner_and_hub_staff_only(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    who = await staff(api, google, db_session)
    order_id, _ = await placed_order(db_session, who["priya_id"], reach=DELIVERED)
    path = f"/v1/orders/{order_id}/events"

    mine = await api.get(path, headers=who["priya"])
    as_hub = await api.get(path, headers=who["hub"])

    assert mine.status_code == 200, mine.text
    assert mine.headers["cache-control"] == "no-store"
    body = mine.json()
    order = await orders_api.get(db_session, order_id)
    assert body == {
        "items": await rows(db_session, order_id),
        "status": "delivered",
        "version": order.version,
        "eta": None,
    }
    statuses = [i["status"] for i in body["items"]]
    assert statuses == ["approved", *DELIVERED]
    assert [i["actor"] for i in body["items"]][1:] == ["system"] + ["hub"] * 5
    assert all(set(i) == {"id", "status", "at", "actor"} for i in body["items"])  # no user ids
    assert as_hub.json() == body
    # The order detail's timeline is the same rows, without their ids (§3.1).
    detail = await api.get(f"/v1/orders/{order_id}", headers=who["priya"])
    assert detail.json()["timeline"] == [
        {k: v for k, v in i.items() if k != "id"} for i in body["items"]
    ]

    ramesh, _ = await as_user(api, google, RAMESH)  # a customer of another household
    assert (await api.get(path, headers=ramesh)).json()["code"] == "orders.not_found"
    assert (await api.get(path, headers=who["other"])).status_code == 403
    unknown = await api.get(f"/v1/orders/{uuid.uuid4()}/events", headers=who["priya"])
    assert (unknown.status_code, unknown.json()["code"]) == (404, "orders.not_found")
    hidden = await api.get(path, headers={**ramesh, **SSE})
    assert (hidden.status_code, hidden.json()["code"]) == (404, "orders.not_found")


@time_machine.travel(NOW, tick=False)
async def test_the_stream_sends_a_snapshot_then_ends_at_a_final_status(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, valkey: Redis
) -> None:
    api = clients(valkey=valkey)
    who = await staff(api, google, db_session)
    order_id, _ = await placed_order(db_session, who["priya_id"], reach=DELIVERED)
    timeline = await rows(db_session, order_id)
    version = (await orders_api.get(db_session, order_id)).version

    answer = await api.get(f"/v1/orders/{order_id}/events", headers={**who["priya"], **SSE})

    assert answer.status_code == 200, answer.text
    assert answer.headers["content-type"].startswith("text/event-stream")
    assert answer.headers["cache-control"] == "no-store"
    assert answer.headers["x-accel-buffering"] == "no"
    snapshot, end = parse(answer.text)
    assert snapshot == {
        "event": "snapshot",
        "id": timeline[-1]["id"],  # the browser resumes after the newest row
        "data": {
            "order_id": str(order_id),
            "status": "delivered",
            "version": version,
            "timeline": timeline,
            "eta": None,
        },
    }
    assert end == {"event": "end", "data": {"status": "delivered"}}
    # The stream's slot was given back.
    assert int(await valkey.get(f"gb:orders:streams:{who['priya_id']}") or 0) == 0


@time_machine.travel(NOW, tick=False)
async def test_a_reconnect_gets_the_rows_after_last_event_id_and_no_snapshot(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    who = await staff(api, google, db_session)
    order_id, _ = await placed_order(db_session, who["priya_id"], reach=DELIVERED)
    timeline = await rows(db_session, order_id)
    version = (await orders_api.get(db_session, order_id)).version
    path = f"/v1/orders/{order_id}/events"

    resumed = await api.get(path, headers={**who["hub"], **SSE, "Last-Event-ID": timeline[4]["id"]})

    frames = parse(resumed.text)
    assert frames == [
        *(
            {"event": "status", "id": row["id"], "data": {**row, "version": version}}
            for row in timeline[5:]
        ),
        {"event": "end", "data": {"status": "delivered"}},
    ]
    # An id that is not one of this order's rows starts over with a snapshot.
    for bad in ("not-a-uuid", str(uuid.uuid4())):
        fresh = await api.get(path, headers={**who["priya"], **SSE, "Last-Event-ID": bad})
        assert [f["event"] for f in parse(fresh.text)] == ["snapshot", "end"]


@time_machine.travel(NOW, tick=False)
async def test_a_fourth_open_stream_is_refused(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, valkey: Redis
) -> None:
    api = clients(valkey=valkey)
    who = await staff(api, google, db_session)
    order_id, _ = await placed_order(db_session, who["priya_id"], reach=DELIVERED)
    key = f"gb:orders:streams:{who['priya_id']}"
    await valkey.set(key, 3)

    refused = await api.get(f"/v1/orders/{order_id}/events", headers={**who["priya"], **SSE})
    polled = await api.get(f"/v1/orders/{order_id}/events", headers=who["priya"])

    assert (refused.status_code, refused.json()["code"]) == (429, "orders.too_many_streams")
    assert refused.headers["retry-after"] == "20"
    assert polled.status_code == 200  # polling is never limited
    assert int(await valkey.get(key) or 0) == 3
    await valkey.set(key, 2)
    allowed = await api.get(f"/v1/orders/{order_id}/events", headers={**who["priya"], **SSE})
    assert allowed.status_code == 200
    assert int(await valkey.get(key) or 0) == 2


class FakeTime:
    """A clock that only moves when the stream sleeps; `on_sleep` runs at chosen sleeps."""

    def __init__(self, on_sleep: dict[int, Any]) -> None:
        self.now = 0.0
        self.sleeps = 0
        self.on_sleep = on_sleep

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds
        self.sleeps += 1
        if self.sleeps in self.on_sleep:
            await self.on_sleep[self.sleeps]()


async def drain(stream: Any) -> list[dict[str, Any]]:
    return parse("".join([chunk.decode() async for chunk in stream]))


@time_machine.travel(NOW, tick=False)
async def test_the_stream_polls_for_new_rows_pings_when_quiet_and_ends_when_delivered(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    api = clients()
    who = await staff(api, google, db_session)
    order_id, _ = await placed_order(db_session, who["priya_id"], reach=DELIVERED[:4])

    async def move(to: str, actor: str) -> None:
        await orders_api.transition(
            db_session, order_id, to, actor=actor, actor_user_id=uuid.uuid4()
        )

    async def at_society() -> None:
        await move("at_society", "captain")

    async def delivered() -> None:
        await move("delivered", "captain")

    fake = FakeTime({1: at_society, 9: delivered})  # 8 quiet polls (24 s) between the two

    frames = await drain(
        live.stream(db_session, order_id, None, sleep=fake.sleep, clock=fake.clock)
    )

    events = [f.get("event", "ping" if f.get("comment") == "ping" else "?") for f in frames]
    assert events == ["snapshot", "status", "ping", "status", "end"]
    assert [f["data"]["status"] for f in frames if f.get("event") == "status"] == [
        "at_society",
        "delivered",
    ]
    assert {f["data"]["actor"] for f in frames if f.get("event") == "status"} == {"captain"}
    assert "actor_user_id" not in frames[1]["data"]
    order = await orders_api.get(db_session, order_id)
    assert frames[3]["data"]["version"] == order.version
    assert frames[-1]["data"] == {"status": "delivered"}


@time_machine.travel(NOW, tick=False)
async def test_the_stream_closes_after_15_minutes_without_end(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession, monkeypatch: Any
) -> None:
    api = clients()
    who = await staff(api, google, db_session)
    order_id, _ = await placed_order(db_session, who["priya_id"], reach=("locked",))
    monkeypatch.setattr(live, "MAX_S", 30.0)
    fake = FakeTime({})

    frames = await drain(
        live.stream(db_session, order_id, None, sleep=fake.sleep, clock=fake.clock)
    )

    assert [f.get("event", "ping") for f in frames] == ["snapshot", "ping"]
    assert fake.now == 30.0
