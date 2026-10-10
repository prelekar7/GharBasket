"""Transactional outbox: publish an event in the same transaction as the change it describes.

If the transaction rolls back, the event disappears with it; if it commits, the event is delivered
(at least once) by the relay. Consumers are plain async functions registered with `subscribe`;
they run inside the relay's transaction and must be idempotent (they may see an event twice).

How events travel (ADR-0021, no broker):
- after a request that published something commits, the API relays fresh rows itself as a
  background task (`relay_soon`), so events usually go out within a second;
- the `outbox_sweep` job (platform.jobs) picks up what that missed: a restart, or a failed
  handler (retried with backoff).

An event with no consumer is marked sent: the outbox is a delivery queue, not a replay log.
"""

import asyncio
import re
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gharbasket.platform.models import OutboxEvent

# "<module>.<event>", e.g. "orders.locked", "identity.household_created".
_EVENT_TYPE = re.compile(r"^[a-z][a-z_]*\.[a-z][a-z_]*$")
Handler = Callable[[AsyncSession, OutboxEvent], Awaitable[None]]
CONSUMERS: dict[str, list[Handler]] = defaultdict(list)
# Retry a failed delivery after 30 s, 1 min, 2 min … up to an hour.
BACKOFF_BASE = timedelta(seconds=30)
BACKOFF_MAX = timedelta(hours=1)
ERROR_LENGTH = 500
log = structlog.get_logger("gharbasket.outbox")


async def publish(session: AsyncSession, event_type: str, payload: dict[str, Any]) -> uuid.UUID:
    """Add an event to the outbox; it is sent only if the caller's transaction commits."""
    if not _EVENT_TYPE.fullmatch(event_type):
        raise ValueError(f"event type must look like 'module.event', got {event_type!r}")
    event = OutboxEvent(event_type=event_type, payload=payload)
    session.add(event)
    await session.flush()  # assigns the uuidv7 id from the database
    session.info["outbox_published"] = True
    return event.id


def subscribe(event_type: str, handler: Handler) -> Handler:
    """Register a consumer for `event_type` (modules do this at import time, in their jobs.py)."""
    if not _EVENT_TYPE.fullmatch(event_type):
        raise ValueError(f"event type must look like 'module.event', got {event_type!r}")
    if handler not in CONSUMERS[event_type]:
        CONSUMERS[event_type].append(handler)
    return handler


def backoff(attempts: int) -> timedelta:
    return min(BACKOFF_BASE * 2 ** max(attempts - 1, 0), BACKOFF_MAX)


@dataclass(frozen=True)
class Relayed:
    sent: int
    failed: int


async def relay(session: AsyncSession, *, limit: int = 100) -> Relayed:
    """Deliver unsent events that are due, oldest first, and commit.

    Rows are taken with `FOR UPDATE SKIP LOCKED`, so the after-commit relay and the sweep job can
    run at the same time without delivering an event twice. Each event's handlers run inside a
    savepoint: a failing handler's writes roll back, the event stays unsent with a backoff, and
    the others still go out.
    """
    # clock_timestamp(), not now(): now() is when the transaction began, so a long transaction
    # (a job, or a test) would treat events that fell due since then as not yet due.
    rows = await session.execute(
        select(OutboxEvent)
        .where(OutboxEvent.sent_at.is_(None), OutboxEvent.available_at <= func.clock_timestamp())
        .order_by(OutboxEvent.id)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    sent = failed = 0
    for event in rows.scalars():
        handlers = CONSUMERS.get(event.event_type, [])
        try:
            async with session.begin_nested():
                for handler in handlers:
                    await handler(session, event)
        except Exception as exc:
            failed += 1
            event.attempts += 1
            event.available_at = datetime.now(UTC) + backoff(event.attempts)
            event.last_error = f"{type(exc).__name__}: {exc}"[:ERROR_LENGTH]
            log.warning(
                "outbox_delivery_failed",
                event_type=event.event_type,
                event_id=str(event.id),
                attempts=event.attempts,
                error=type(exc).__name__,
            )
            continue
        sent += 1
        event.sent_at = datetime.now(UTC)
    await session.commit()
    return Relayed(sent, failed)


# --- the after-commit relay -------------------------------------------------------------------

_tasks: set[asyncio.Task[None]] = set()


def relay_soon(sessionmaker: async_sessionmaker[AsyncSession]) -> asyncio.Task[None]:
    """Relay in the background, after the request that published has finished."""

    async def run() -> None:
        try:
            async with sessionmaker() as session:
                await relay(session)
        except Exception as exc:  # the sweep job retries; a request must never fail for this
            log.warning("outbox_relay_failed", error=type(exc).__name__)

    task = asyncio.create_task(run())
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return task


async def drain() -> None:
    """Wait for background relays (tests, and the app's shutdown)."""
    if _tasks:
        await asyncio.gather(*_tasks, return_exceptions=True)
