"""GPS pings and the route's latest position (spec §3.3).

A rider's phone sends batches of pings while its route is in progress. Each accepted ping goes to
a Valkey stream (`persist_gps_pings` moves it into `logistics.gps_pings` every minute), the
route's latest position is set (`gb:loc:<route_id>`, 15 minutes) and published on
`gb:live:<route_id>`. Without Valkey the pings go into Postgres at once and the latest position is
read from there. `logistics.gps_pings` is partitioned by month: a month's partition is made when
its first ping is written and dropped by `purge_gps_pings` once all its pings are 90 days old.
Coordinates are never logged.
"""

import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import structlog
from geoalchemy2 import Geometry
from redis.asyncio import Redis
from sqlalchemy import cast, delete, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.logistics.models import GpsPing, Route
from gharbasket.modules.logistics.schemas import PingIn
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.geo import LAT_RANGE, LNG_RANGE, point

log = structlog.get_logger("gharbasket.logistics")

STREAM = "gb:pings"
LOC_TTL_S = 15 * 60
MAX_AGE = timedelta(minutes=10)
MAX_AHEAD = timedelta(minutes=1)
MIN_GAP = timedelta(seconds=5)
MAX_ACCURACY_M = 200
BATCH = 500


@dataclass(frozen=True)
class Position:
    at: datetime
    lat: float
    lng: float
    heading: int | None


def loc_key(route_id: uuid.UUID) -> str:
    return f"gb:loc:{route_id}"


def channel(route_id: uuid.UUID) -> str:
    return f"gb:live:{route_id}"


async def active_route(session: AsyncSession, rider_id: uuid.UUID, route_id: uuid.UUID) -> Route:
    """The caller's route while it is in progress, else 409 `logistics.no_active_route`."""
    route = await session.get(Route, route_id)
    if route is None or route.rider_user_id != rider_id or route.status != "in_progress":
        raise ProblemError(
            409, "logistics.no_active_route", "Location is taken only during your started route."
        )
    return route


def keep(pings: Sequence[PingIn], last_at: datetime | None, now: datetime) -> list[PingIn]:
    """The pings worth keeping: recent, inside India, accurate enough, 5 s apart."""
    kept = []
    for ping in sorted(pings, key=lambda p: p.at):
        if not now - MAX_AGE <= ping.at <= now + MAX_AHEAD:
            continue
        inside = (
            LAT_RANGE[0] <= ping.lat <= LAT_RANGE[1] and LNG_RANGE[0] <= ping.lng <= LNG_RANGE[1]
        )
        if not inside or ping.accuracy_m > MAX_ACCURACY_M:
            continue
        if last_at is not None and ping.at < last_at + MIN_GAP:
            continue
        kept.append(ping)
        last_at = ping.at
    return kept


def _fields(rider_id: uuid.UUID, route_id: uuid.UUID, ping: PingIn) -> dict[str, str]:
    return {
        "rider": str(rider_id),
        "route": str(route_id),
        "at": ping.at.isoformat(),
        "lat": repr(ping.lat),
        "lng": repr(ping.lng),
        "acc": str(ping.accuracy_m),
        "speed": "" if ping.speed_mps is None else repr(ping.speed_mps),
        "heading": "" if ping.heading is None else str(ping.heading),
    }


async def _to_valkey(
    valkey: Redis, rider_id: uuid.UUID, route_id: uuid.UUID, pings: list[PingIn]
) -> bool:
    last = pings[-1]
    position = {
        "at": last.at.isoformat(),
        "lat": last.lat,
        "lng": last.lng,
        "heading": last.heading,
    }
    try:
        async with valkey.pipeline(transaction=True) as pipe:
            for ping in pings:
                pipe.xadd(STREAM, _fields(rider_id, route_id, ping))  # pyright: ignore[reportArgumentType]
            pipe.set(loc_key(route_id), json.dumps(position), ex=LOC_TTL_S)
            pipe.publish(channel(route_id), json.dumps(position))
            await pipe.execute()
    except Exception as exc:
        log.warning("gps_valkey_skipped", error=type(exc).__name__)
        return False
    return True


async def _partition(session: AsyncSession, at: datetime) -> None:
    """The month's partition of `gps_pings`, made on its first ping."""
    start = date(at.year, at.month, 1)
    end = date(at.year + at.month // 12, at.month % 12 + 1, 1)
    await session.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS logistics.gps_pings_{start:%Y%m} PARTITION OF "
            f"logistics.gps_pings FOR VALUES FROM ('{start}T00:00:00+00') TO ('{end}T00:00:00+00')"
        )
    )


async def insert_pings(session: AsyncSession, rows: list[dict[str, Any]]) -> int:
    """Write pings; one already there (the same rider and time) is left as it is."""
    if not rows:
        return 0
    for month in {(r["at"].year, r["at"].month) for r in rows}:
        await _partition(session, datetime(*month, 1, tzinfo=UTC))
    done = await session.execute(
        insert(GpsPing)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["rider_user_id", "at"])
        .returning(GpsPing.at)
    )
    return len(done.all())


def _row(rider_id: uuid.UUID, route_id: uuid.UUID, ping: PingIn) -> dict[str, Any]:
    return {
        "rider_user_id": rider_id,
        "route_id": route_id,
        "at": ping.at.astimezone(UTC),
        "location": point(ping.lat, ping.lng),
        "accuracy_m": ping.accuracy_m,
        "speed_mps": ping.speed_mps,
        "heading": ping.heading,
    }


async def accept(
    session: AsyncSession,
    valkey: Redis | None,
    route: Route,
    rider_id: uuid.UUID,
    pings: Sequence[PingIn],
) -> int:
    """Keep the batch's good pings; returns how many were accepted."""
    last = await latest(session, valkey, route.id)
    kept = keep(pings, None if last is None else last.at, datetime.now(UTC))
    if kept and (valkey is None or not await _to_valkey(valkey, rider_id, route.id, kept)):
        await insert_pings(session, [_row(rider_id, route.id, p) for p in kept])
    return len(kept)


async def latest(
    session: AsyncSession, valkey: Redis | None, route_id: uuid.UUID
) -> Position | None:
    """The route's latest position: from Valkey, else the latest persisted ping."""
    if valkey is not None:
        try:
            raw = await valkey.get(loc_key(route_id))
        except Exception as exc:
            log.warning("gps_valkey_skipped", error=type(exc).__name__)
            raw = None
        if raw:
            found = json.loads(raw)
            return Position(
                datetime.fromisoformat(found["at"]), found["lat"], found["lng"], found["heading"]
            )
    geometry = cast(GpsPing.location, Geometry)
    row = (
        await session.execute(
            select(GpsPing.at, func.ST_Y(geometry), func.ST_X(geometry), GpsPing.heading)
            .where(GpsPing.route_id == route_id)
            .order_by(GpsPing.at.desc())
            .limit(1)
        )
    ).first()
    return None if row is None else Position(row[0], float(row[1]), float(row[2]), row[3])


async def forget(valkey: Redis | None, route_id: uuid.UUID) -> None:
    """Drop a closed route's live position."""
    if valkey is None:
        return
    try:
        await valkey.delete(loc_key(route_id))
    except Exception as exc:
        log.warning("gps_valkey_skipped", error=type(exc).__name__)


# --- jobs -----------------------------------------------------------------------------------------


def _text(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


def _from_stream(fields: Any) -> dict[str, Any]:
    f = {_text(k): _text(v) for k, v in fields.items()}
    lat, lng = float(f["lat"]), float(f["lng"])
    return {
        "rider_user_id": uuid.UUID(f["rider"]),
        "route_id": uuid.UUID(f["route"]),
        "at": datetime.fromisoformat(f["at"]).astimezone(UTC),
        "location": point(lat, lng),
        "accuracy_m": int(f["acc"]),
        "speed_mps": float(f["speed"]) if f["speed"] else None,
        "heading": int(f["heading"]) if f["heading"] else None,
    }


async def persist(session: AsyncSession, valkey: Redis | None) -> tuple[int, int]:
    """Move the Valkey stream into `gps_pings`: (pings read, rows inserted)."""
    if valkey is None:
        return 0, 0
    read = inserted = 0
    while True:
        entries = await valkey.xrange(STREAM, count=BATCH)
        if not entries:
            return read, inserted
        inserted += await insert_pings(session, [_from_stream(fields) for _, fields in entries])
        await valkey.xdel(STREAM, *[entry_id for entry_id, _ in entries if entry_id is not None])
        read += len(entries)


async def purge(session: AsyncSession, cutoff: datetime) -> tuple[int, int]:
    """Delete pings older than `cutoff`, then drop the month partitions that ended before it:
    (pings deleted, partitions dropped)."""
    deleted = await session.execute(
        delete(GpsPing).where(GpsPing.at < cutoff).returning(GpsPing.at)
    )
    names = await session.execute(
        text(
            "SELECT c.relname FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
            "JOIN pg_class p ON p.oid = i.inhparent JOIN pg_namespace n ON n.oid = p.relnamespace "
            "WHERE n.nspname = 'logistics' AND p.relname = 'gps_pings'"
        )
    )
    dropped = 0
    for (name,) in names.all():
        year, month = int(name[-6:-2]), int(name[-2:])
        ends = datetime(year + month // 12, month % 12 + 1, 1, tzinfo=UTC)
        if ends <= cutoff:
            await session.execute(text(f"DROP TABLE logistics.{name}"))
            dropped += 1
    return len(deleted.all()), dropped
