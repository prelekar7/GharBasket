"""The live order timeline (M4 §3.3): `GET /v1/orders/{id}/events` as JSON for clients that poll,
or as a Server-Sent Events stream.

The stream reads `orders.order_events` every 3 seconds (no pub/sub in v1) and ends its read
transaction after each poll, so an open stream holds a session but not a pooled connection. It
closes after 15 minutes, or with `end` once the order is delivered, cancelled or refunded; the
client reconnects with `Last-Event-ID`. At most 3 streams per user are open at once, counted in
Valkey (no limit when Valkey is not configured or does not answer).
"""

import json
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable

import anyio
import structlog
from fastapi.sse import format_sse_event
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.orders import service
from gharbasket.modules.orders.models import OrderEvent
from gharbasket.modules.orders.schemas import OrderEventsOut, TimelineEntry, TimelineEvent
from gharbasket.platform.errors import ProblemError

log = structlog.get_logger("gharbasket.orders.live")

FINAL = frozenset({"delivered", "cancelled", "refunded"})
POLL_S = 3.0
PING_S = 20.0
MAX_S = 15 * 60.0
MAX_STREAMS = 3


def entry(row: OrderEvent) -> TimelineEntry:
    """A timeline row as the customer sees it: the kind of actor, never the person."""
    return TimelineEntry(status=row.to_status, at=row.at, actor=row.actor)  # pyright: ignore[reportArgumentType]


def event(row: OrderEvent) -> TimelineEvent:
    """The same row with its id, for the live timeline and Last-Event-ID."""
    return TimelineEvent(id=row.id, **entry(row).model_dump())


async def history(session: AsyncSession, order_id: uuid.UUID) -> OrderEventsOut:
    status, version = await service.status_of(session, order_id)
    rows = await service.timeline_of(session, order_id)
    items = [event(r) for r in rows]
    return OrderEventsOut(items=items, status=status, version=version, eta=None)  # pyright: ignore[reportArgumentType]


async def resume_point(
    session: AsyncSession, order_id: uuid.UUID, header: str | None
) -> uuid.UUID | None:
    """The row a reconnecting client last saw; None (send a snapshot) when the header is
    missing, malformed or names a row of another order."""
    if not header:
        return None
    try:
        last = uuid.UUID(header.strip())
    except ValueError:
        return None
    return last if await service.has_event(session, order_id, last) else None


def _sse(event: str, data: object, event_id: uuid.UUID | None = None) -> bytes:
    return format_sse_event(
        data_str=json.dumps(data, separators=(",", ":")),
        event=event,
        id=None if event_id is None else str(event_id),
    )


def _status(row: OrderEvent, version: int) -> bytes:
    return _sse("status", {**event(row).model_dump(mode="json"), "version": version}, row.id)


async def stream(
    session: AsyncSession,
    order_id: uuid.UUID,
    after: uuid.UUID | None,
    *,
    sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> AsyncIterator[bytes]:
    """The SSE body: a snapshot (or, on reconnect, the rows after `after`), then one `status`
    per new row, `: ping` after 20 s of silence, and `end` at a final status."""
    started = last_sent = clock()
    # Status first, then rows: a final status read here always has its row in the next query.
    status, version = await service.status_of(session, order_id)
    rows = await service.timeline_of(session, order_id, after=after)
    await session.commit()  # read-only; gives the connection back to the pool between polls
    if after is None:
        yield _sse(
            "snapshot", _snapshot(order_id, status, version, rows), rows[-1].id if rows else None
        )
    else:
        for row in rows:
            yield _status(row, version)
    if rows:
        after = rows[-1].id
    while status not in FINAL:
        if clock() - started >= MAX_S:
            return  # no `end`: the client reconnects while the screen is open
        await sleep(POLL_S)
        status, version = await service.status_of(session, order_id)
        rows = await service.timeline_of(session, order_id, after=after)
        await session.commit()
        for row in rows:
            yield _status(row, version)
        if rows:
            after, last_sent = rows[-1].id, clock()
        elif clock() - last_sent >= PING_S:
            yield format_sse_event(comment="ping")
            last_sent = clock()
    yield _sse("end", {"status": status})


def _snapshot(
    order_id: uuid.UUID, status: str, version: int, rows: list[OrderEvent]
) -> dict[str, object]:
    return {
        "order_id": str(order_id),
        "status": status,
        "version": version,
        "timeline": [event(r).model_dump(mode="json") for r in rows],
        "eta": None,
    }


def _streams_key(user_id: uuid.UUID) -> str:
    return f"gb:orders:streams:{user_id}"


async def claim(valkey: Redis | None, user_id: uuid.UUID) -> bool:
    """Count one open stream for the user; 429 `orders.too_many_streams` past 3. True when a
    stream was counted (and must be released)."""
    if valkey is None:
        return False
    key = _streams_key(user_id)
    try:
        async with valkey.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, int(MAX_S) + 60)  # a crashed process cannot leak a slot for long
            count, _ = await pipe.execute()
        full = int(count) > MAX_STREAMS
        if full:
            await valkey.decr(key)
    except Exception as exc:
        log.warning("stream_count_skipped", error=type(exc).__name__)
        return False
    if full:
        raise ProblemError(
            429,
            "orders.too_many_streams",
            f"At most {MAX_STREAMS} live order streams are open at once.",
            headers={"Retry-After": "20"},
        )
    return True


async def release(valkey: Redis | None, user_id: uuid.UUID) -> None:
    if valkey is None:
        return
    with anyio.CancelScope(shield=True):  # runs while a disconnect is cancelling the stream
        try:
            await valkey.decr(_streams_key(user_id))
        except Exception as exc:
            log.warning("stream_release_skipped", error=type(exc).__name__)


async def counted(
    body: AsyncIterator[bytes], valkey: Redis | None, user_id: uuid.UUID
) -> AsyncIterator[bytes]:
    """`body`, releasing the user's stream slot however it ends."""
    try:
        async for chunk in body:
            yield chunk
    finally:
        await release(valkey, user_id)
