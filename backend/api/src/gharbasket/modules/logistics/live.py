"""Live tracking (spec §3.3): `GET /v1/routes/{id}/live` as Server-Sent Events.

Who sees what: the route's rider and its hub's managers see every stop and every position; the
captain of a stop's society sees her stop (with `stops_before`) and the van until her stop is
handed over; a customer sees the same for the stop of their household's order, positions rounded
to 4 decimals (about 11 m) and at most every 15 s. The first event is always `snapshot`, so a
reconnect needs no `Last-Event-ID`. The stream reads the route and its stops and the latest
position every 5 s (15 s without Valkey) and ends its read transaction after each read; it closes
with `end` after 30 minutes (`reconnect`), at the handover of the viewer's stop (`handed_over`)
or when the route is completed (`route_completed`). At most 3 streams per user are open at once
(a sorted set of open streams in Valkey, pruned of streams past the 30-minute limit).
"""

import json
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import anyio
import structlog
from fastapi.sse import format_sse_event
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community import api as community_api
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.logistics import attempts, tracking
from gharbasket.modules.logistics.models import Delivery, Route, RouteStop
from gharbasket.modules.logistics.views import eta_now, stops_of
from gharbasket.modules.orders import api as orders_api
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.permissions import Permit, forbidden

log = structlog.get_logger("gharbasket.logistics.live")

POLL_S = 5.0
SLOW_POLL_S = 15.0  # without Valkey: the latest persisted ping
CUSTOMER_EVERY_S = 15.0
PING_S = 20.0
MAX_S = 30 * 60.0
MAX_STREAMS = 3
ON_ROUTE = ("packed", "dispatched", "at_society", "delivered", "undelivered")


@dataclass(frozen=True)
class Audience:
    stop_id: uuid.UUID | None  # None: the whole route (its rider, a hub manager)
    customer: bool = False
    tiffin_id: uuid.UUID | None = None  # a customer's tiffin: watched while the rider has it


async def audience(session: AsyncSession, permit: Permit, route: Route) -> Audience:
    """What the caller may watch on this route, else 403 `auth.forbidden`."""
    user_id = permit.principal.user_id
    for grant in permit.grants:
        if grant.role == "hub_manager" and grant.scope_id == route.hub_id:
            return Audience(None)
        if grant.role == "rider" and route.rider_user_id == user_id:
            return Audience(None)
    stops = [s for s in await stops_of(session, route.id) if s.society_id is not None]
    for grant in permit.grants:
        if grant.role != "captain" or grant.scope_id is None:
            continue
        mine = [s for s in stops if s.society_id == grant.scope_id]
        if mine and await community_api.is_active_captain(session, user_id, grant.scope_id):
            return Audience(mine[0].id)
    if any(g.role == "customer" for g in permit.grants):
        household = await identity_api.household_for_user(session, user_id)
        if household is not None and route.kind == "tiffin":
            # §3.7: "my tiffin is collected and on this route"
            for attempt in await _tiffins_riding(session, [s.id for s in stops]):
                order = await orders_api.get(session, attempt.order_id)
                if order.household_id == household.id:
                    return Audience(attempt.stop_id, customer=True, tiffin_id=order.id)
        elif household is not None:
            pairs = {(s.society_id, s.slot_id): s.id for s in stops}
            orders = await orders_api.orders_for_delivery(
                session, route.hub_id, route.delivery_date, ON_ROUTE
            )
            for summary in orders:
                order = summary.order
                stop_id = pairs.get((order.society_id, order.slot_id))
                if order.household_id == household.id and stop_id is not None:
                    return Audience(stop_id, customer=True)
            # A redelivery of an earlier day rides this route too: its order is not one of the
            # route's date, but the attempt is booked on one of its stops.
            for again in await attempts.riding(session, [s.id for s in stops]):
                order = await orders_api.get(session, again.order_id)
                if order.household_id == household.id and order.status in ON_ROUTE:
                    return Audience(again.stop_id, customer=True)
    raise forbidden()


async def _tiffins_riding(session: AsyncSession, stop_ids: list[uuid.UUID]) -> list[Delivery]:
    found = await session.execute(
        select(Delivery).where(
            Delivery.stop_id.in_(stop_ids),
            Delivery.order_kind == "tiffin",
            Delivery.status == "on_route",
        )
    )
    return list(found.scalars())


def _sse(event: str, data: object) -> bytes:
    return format_sse_event(data_str=json.dumps(data, separators=(",", ":")), event=event)


def _position(found: tracking.Position, customer: bool) -> dict[str, Any]:
    digits = 4 if customer else 6
    return {
        "at": found.at.isoformat(),
        "lat": round(found.lat, digits),
        "lng": round(found.lng, digits),
        "heading": found.heading,
        "age_s": max(0, int((datetime.now(UTC) - found.at).total_seconds())),
    }


@dataclass
class _View:
    status: str
    stops: dict[int, dict[str, Any]]  # seq -> StopLive
    stops_before: int | None
    own_status: str | None
    position: tracking.Position | None


def _next_is_kitchen(stops: list[RouteStop]) -> bool:
    """The first stop still open (`pending` or `arrived`) is a kitchen stop."""
    for stop in sorted(stops, key=lambda s: s.seq):
        if stop.status in ("pending", "arrived"):
            return stop.kind == "kitchen"
    return False


async def _read(
    session: AsyncSession, valkey: Redis | None, route_id: uuid.UUID, who: Audience
) -> _View:
    route = await session.get(Route, route_id, populate_existing=True)
    assert route is not None
    stops: list[RouteStop] = await stops_of(session, route_id)
    etas = eta_now(route, stops)
    live = {s.seq: {"seq": s.seq, "status": s.status, "eta_now": etas[s.id]} for s in stops}
    before = own = None
    if who.stop_id is not None:
        [mine] = [s for s in stops if s.id == who.stop_id]
        before = sum(s.seq < mine.seq and s.status in ("pending", "arrived") for s in stops)
        live, own = {mine.seq: live[mine.seq]}, mine.status
    if who.tiffin_id is not None:  # handed to the captain, delivered or undelivered: it ends
        attempt = await attempts.latest(session, who.tiffin_id)
        if attempt is None or attempt.status != "on_route":
            own = "handed_over"
    position = None
    # The viewer's stop is done: no more positions for a captain or a customer. On a tiffin route
    # neither sees the rider while his next open stop is a kitchen: his position would show where
    # it is (§3.7, Privacy); hub staff and the rider's own stream (`stop_id` None) are unaffected.
    at_kitchen = who.stop_id is not None and _next_is_kitchen(stops)
    if route.status == "in_progress" and own not in ("handed_over", "skipped") and not at_kitchen:
        position = await tracking.latest(session, valkey, route_id)
    await session.commit()  # read-only; gives the connection back between reads
    return _View(route.status, live, before, own, position)


def _end(view: _View) -> str | None:
    if view.own_status == "handed_over":
        return "handed_over"
    if view.status == "completed":
        return "route_completed"
    if view.status == "cancelled" or view.own_status == "skipped":
        return "access_ended"
    return None


async def stream(
    session: AsyncSession,
    valkey: Redis | None,
    route_id: uuid.UUID,
    who: Audience,
    *,
    sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> AsyncIterator[bytes]:
    """`snapshot`, then `stop` per change, `position` per new ping, `: ping` after 20 s of
    silence and `end`."""
    started = last_sent = last_position = clock()
    view = await _read(session, valkey, route_id, who)
    snapshot: dict[str, Any] = {
        "route_id": str(route_id),
        "status": view.status,
        "position": None if view.position is None else _position(view.position, who.customer),
        "stops": list(view.stops.values()),
    }
    if view.stops_before is not None:
        snapshot["stops_before"] = view.stops_before
    yield _sse("snapshot", snapshot)
    poll = POLL_S if valkey is not None else SLOW_POLL_S
    sent_at = None if view.position is None else view.position.at
    while (reason := _end(view)) is None:
        if clock() - started >= MAX_S:
            reason = "reconnect"
            break
        await sleep(poll)
        previous, view = view, await _read(session, valkey, route_id, who)
        chunks = [_sse("stop", s) for seq, s in view.stops.items() if previous.stops.get(seq) != s]
        fresh = view.position is not None and view.position.at != sent_at
        if fresh and (not who.customer or clock() - last_position >= CUSTOMER_EVERY_S):
            assert view.position is not None
            chunks.append(_sse("position", _position(view.position, who.customer)))
            sent_at, last_position = view.position.at, clock()
        for chunk in chunks:
            yield chunk
        if chunks:
            last_sent = clock()
        elif clock() - last_sent >= PING_S:
            yield format_sse_event(comment="keep-alive")
            last_sent = clock()
    yield _sse("end", {"reason": reason})


def _streams_key(user_id: uuid.UUID) -> str:
    return f"gb:logistics:streams:{user_id}"


async def claim(valkey: Redis | None, user_id: uuid.UUID) -> str | None:
    """Open one stream for the user: a member of a sorted set scored by its start, pruned of
    streams older than the 30-minute limit (a crashed process cannot hold a slot longer).
    429 `logistics.too_many_streams` past 3. Returns the stream's id to release, or None when
    nothing was counted (no Valkey)."""
    if valkey is None:
        return None
    key, stream_id, now = _streams_key(user_id), uuid.uuid4().hex, time.time()
    try:
        async with valkey.pipeline(transaction=True) as pipe:
            pipe.zremrangebyscore(key, "-inf", now - MAX_S - 60)
            pipe.zadd(key, {stream_id: now})
            pipe.zcard(key)
            pipe.expire(key, int(MAX_S) + 120)
            _, _, count, _ = await pipe.execute()
        full = int(count) > MAX_STREAMS
        if full:
            await valkey.zrem(key, stream_id)
    except Exception as exc:
        log.warning("stream_count_skipped", error=type(exc).__name__)
        return None
    if full:
        raise ProblemError(
            429,
            "logistics.too_many_streams",
            f"At most {MAX_STREAMS} live route streams are open at once.",
            headers={"Retry-After": "20"},
        )
    return stream_id


async def counted(
    body: AsyncIterator[bytes], valkey: Redis, user_id: uuid.UUID, stream_id: str
) -> AsyncIterator[bytes]:
    """`body`, releasing the user's stream slot however it ends."""
    try:
        async for chunk in body:
            yield chunk
    finally:
        with anyio.CancelScope(shield=True):  # runs while a disconnect cancels the stream
            try:
                await valkey.zrem(_streams_key(user_id), stream_id)
            except Exception as exc:
                log.warning("stream_release_skipped", error=type(exc).__name__)
