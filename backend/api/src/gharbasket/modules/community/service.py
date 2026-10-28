"""Business logic of M9 Community & Kindness: society search, unlock counts and society requests.

Search (spec §3.1) uses one keyset-paginated query with three orders:
- `q` given: best trigram word match first (pg_trgm `<%` on name + area, accent-insensitive),
  then distance when `near` is given, then id;
- only `near`: nearest first;
- neither: alphabetical.
"""

import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from geoalchemy2 import WKTElement
from sqlalchemy import ColumnElement, Float, cast, func, null, or_, select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from gharbasket.modules.community.models import Society, SocietyRequest
from gharbasket.modules.community.schemas import SocietyRequestIn
from gharbasket.modules.identity import api as identity_api
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.geo import LAT_RANGE, LNG_RANGE, LatLng, geography, point
from gharbasket.platform.pagination import decode_cursor, encode_cursor

# Societies without a drawn boundary have no distance; they sort after every real one.
NO_DISTANCE = 1e12


def parse_near(value: str) -> LatLng:
    """`"19.03,73.07"` → LatLng; 422 `community.near_invalid` unless it is a point in India."""
    lat_text, comma, lng_text = value.partition(",")
    try:
        lat, lng = float(lat_text), float(lng_text)
    except ValueError:
        lat = lng = float("nan")
    if not comma or not (
        LAT_RANGE[0] <= lat <= LAT_RANGE[1] and LNG_RANGE[0] <= lng <= LNG_RANGE[1]
    ):
        raise ProblemError(
            422, "community.near_invalid", "`near` must be `lat,lng` for a place in India."
        )
    return LatLng(lat=lat, lng=lng)


@dataclass(frozen=True)
class Found:
    society: Society
    distance_m: int | None


async def search(
    session: AsyncSession,
    *,
    q: str | None,
    near: LatLng | None,
    cursor: str | None,
    limit: int,
) -> tuple[list[Found], str | None]:
    distance: ColumnElement[Any] | None = None
    if near is not None:
        distance = func.ST_Distance(Society.boundary, geography(near.lat, near.lng), type_=Float)

    stmt = select(Society, distance if distance is not None else cast(null(), Float))
    # The sort keys, each with the type its cursor value must have; the order kind goes into the
    # cursor too, so a cursor from one kind of search is refused by another.
    keys: list[ColumnElement[Any] | InstrumentedAttribute[Any]] = []
    kinds: list[type] = []
    if q:
        term = func.community.f_unaccent(func.lower(q))
        stmt = stmt.where(term.op("<%")(Society.search_text))
        keys.append(-func.word_similarity(term, Society.search_text, type_=Float))
        kinds.append(float)
    if distance is not None:
        keys.append(func.coalesce(distance, NO_DISTANCE))
        kinds.append(float)
    if not keys:
        keys.append(Society.name)
        kinds.append(str)
    keys.append(Society.id)
    order = "+".join(name for name, on in (("match", q), ("near", near)) if on) or "name"

    if cursor is not None:
        after = _cursor_position(cursor, order, kinds)
        stmt = stmt.where(tuple_(*keys) > tuple_(*after))
    rows = (await session.execute(stmt.add_columns(*keys).order_by(*keys).limit(limit + 1))).all()

    found = [Found(row[0], None if row[1] is None else round(row[1])) for row in rows[:limit]]
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        next_cursor = encode_cursor({"order": order, "after": [*last[2:-1], str(last[-1])]})
    return found, next_cursor


def _cursor_position(cursor: str, order: str, kinds: list[type]) -> list[Any]:
    invalid = ProblemError(422, "request.invalid_cursor", "The cursor is not valid.")
    position = decode_cursor(cursor)
    after: Any = position.get("after")
    if position.get("order") != order or not isinstance(after, list):
        raise invalid
    *values, last_id = after  # pyright: ignore[reportUnknownVariableType]
    if len(values) != len(kinds) or not all(
        isinstance(value, kind) or (kind is float and type(value) is int)
        for value, kind in zip(values, kinds, strict=True)  # pyright: ignore[reportUnknownArgumentType]
    ):
        raise invalid
    try:
        return [*values, uuid.UUID(str(last_id))]  # pyright: ignore[reportUnknownArgumentType]
    except ValueError as exc:
        raise invalid from exc


async def get(session: AsyncSession, society_id: uuid.UUID) -> Society:
    society = await session.get(Society, society_id)
    if society is None:
        raise ProblemError(404, "community.society_not_found", "No such society.")
    return society


async def joined(session: AsyncSession, society_id: uuid.UUID) -> int:
    return await identity_api.households_in_society(session, society_id)


async def create_request(
    session: AsyncSession, user_id: uuid.UUID, body: SocietyRequestIn
) -> SocietyRequest:
    request = SocietyRequest(
        user_id=user_id,
        name=body.name,
        area=body.area,
        location=None if body.location is None else point(body.location.lat, body.location.lng),
        note=body.note,
    )
    session.add(request)
    await session.flush()
    await session.refresh(request, ["status", "created_at"])
    return request


@dataclass(frozen=True)
class SocietyData:
    """One society as the seed (and later the console editor) describes it."""

    id: uuid.UUID
    slug: str
    name: str
    area: str | None
    boundary: list[tuple[float, float]] | None  # (lat, lng) corners, not closed
    towers: list[dict[str, str]]
    flats: int | None
    unlock_threshold: int
    status: str
    hub_id: uuid.UUID | None
    data_source: str = "real"


def polygon(corners: list[tuple[float, float]]) -> WKTElement:
    ring = [*corners, corners[0]]
    return WKTElement(
        f"POLYGON(({', '.join(f'{lng} {lat}' for lat, lng in ring)}))", srid=4326, extended=False
    )


async def save_society(session: AsyncSession, data: SocietyData) -> tuple[Society, bool]:
    """Create the society, or update the one with the same slug. Returns (society, created)."""
    society = (
        await session.execute(select(Society).where(Society.slug == data.slug))
    ).scalar_one_or_none()
    created = society is None
    if society is None:
        society = Society(id=data.id, slug=data.slug)
        session.add(society)
    society.name = data.name
    society.area = data.area
    society.boundary = None if data.boundary is None else polygon(data.boundary)
    society.towers = data.towers
    society.flats = data.flats
    society.unlock_threshold = data.unlock_threshold
    society.status = data.status
    if data.status == "live" and society.live_at is None:
        society.live_at = datetime.now(UTC)  # a society seeded live has always been live
    society.hub_id = data.hub_id
    society.data_source = data.data_source
    await session.flush()
    return society, created


def _base_slug(name: str) -> str:
    """`Panvel Heights` -> `panvel-heights` (ASCII only; a name with none becomes `society`)."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")[:70].strip("-")
    return slug or "society"


async def create_society(
    session: AsyncSession,
    *,
    name: str,
    area: str | None,
    hub_id: uuid.UUID | None,
    data_source: str = "real",
) -> Society:
    """A new `prospect` society (spec §3.9): unlock threshold 20, no boundary, no towers, flats
    unknown. The slug comes from the name, with `-2`, `-3`... when it is taken. `data_source`
    follows what it was made from (a simulator's request makes a synthetic society)."""
    base = _base_slug(name)
    for _ in range(5):
        taken = set(
            (
                await session.execute(
                    select(Society.slug).where(
                        or_(Society.slug == base, Society.slug.like(f"{base}-%"))
                    )
                )
            ).scalars()
        )
        slug, n = base, 1
        while slug in taken:
            n += 1
            slug = f"{base}-{n}"
        society = Society(
            slug=slug,
            name=name,
            area=area,
            hub_id=hub_id,
            status="prospect",
            data_source=data_source,
        )
        try:
            async with session.begin_nested():  # two accepts of the same name at once
                session.add(society)
                await session.flush()
        except IntegrityError:
            continue
        await session.refresh(society)
        return society
    raise ProblemError(409, "community.slug_taken", "Try again in a moment.")
