"""The staff society list and the society request review (spec §3.9).

Hub managers and admins list societies (hub-less ones are an admin's) and work the queue of
society requests: accept one (it becomes a `prospect` society), mark it a duplicate of a listed
society, or undo that. Nothing here names a captain or a requester.
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from geoalchemy2 import Geometry
from sqlalchemy import Text, case, cast, func, or_, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.community import service
from gharbasket.modules.community.models import Society, SocietyRequest
from gharbasket.modules.community.schemas import (
    AdminSociety,
    GeoPolygon,
    SocietyCreateIn,
    SocietyRequestAdmin,
)
from gharbasket.modules.hub_ops import api as hub_ops_api
from gharbasket.modules.identity import api as identity_api
from gharbasket.platform import audit
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.pagination import decode_cursor, encode_cursor

STATUS_ORDER = ("live", "unlocking", "prospect", "paused")


def invalid_cursor() -> ProblemError:
    return ProblemError(422, "request.invalid_cursor", "The cursor is not valid.")


# --- societies -----------------------------------------------------------------------------------


async def list_societies(
    session: AsyncSession,
    *,
    hub_ids: Sequence[uuid.UUID] | None,
    hub_id: uuid.UUID | None,
    unassigned: bool,
    q: str | None,
    status: str | None,
    cursor: str | None,
    limit: int,
) -> tuple[list[AdminSociety], str | None]:
    """A page of societies: live, unlocking, prospect, paused, then by name.

    `hub_ids` are the hubs the caller serves (None: every hub, an admin); `hub_id` narrows to
    one; `unassigned` adds the societies with no hub (admins only, decided by the caller)."""
    rank = case(*((Society.status == name, n) for n, name in enumerate(STATUS_ORDER)), else_=9)
    stmt = select(Society, func.ST_AsGeoJSON(Society.boundary, type_=Text))
    if hub_id is not None:
        where = Society.hub_id == hub_id
    elif hub_ids is not None:
        where = Society.hub_id.in_(list(hub_ids))
    else:
        where = None
    if unassigned and where is not None:
        where = or_(where, Society.hub_id.is_(None))
    if where is not None:
        stmt = stmt.where(where)
    if status is not None:
        stmt = stmt.where(Society.status == status)
    if q:
        term = func.community.f_unaccent(func.lower(q))
        stmt = stmt.where(term.op("<%")(Society.search_text))
    if cursor is not None:
        after = decode_cursor(cursor).get("after")
        try:
            assert isinstance(after, list) and len(after) == 3  # pyright: ignore[reportUnknownArgumentType]
            position = (
                int(after[0]),  # pyright: ignore[reportUnknownArgumentType]
                str(after[1]),  # pyright: ignore[reportUnknownArgumentType]
                uuid.UUID(str(after[2])),  # pyright: ignore[reportUnknownArgumentType]
            )
        except (AssertionError, ValueError, TypeError):
            raise invalid_cursor() from None
        stmt = stmt.where(tuple_(rank, Society.name, Society.id) > tuple_(*position))
    rows = (
        await session.execute(stmt.order_by(rank, Society.name, Society.id).limit(limit + 1))
    ).all()
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        last = page[-1][0]
        next_cursor = encode_cursor(
            {"after": [STATUS_ORDER.index(last.status), last.name, str(last.id)]}
        )
    return await societies_out(session, [(row[0], row[1]) for row in page]), next_cursor


async def societies_out(
    session: AsyncSession, found: list[tuple[Society, str | None]]
) -> list[AdminSociety]:
    """Hubs' names and the household counts are read once for the whole page."""
    counts = await identity_api.households_in_societies(session, [s.id for s, _ in found])
    hubs: dict[uuid.UUID, str | None] = {}
    for hub_id in {s.hub_id for s, _ in found if s.hub_id}:
        hub = await hub_ops_api.get_hub(session, hub_id)
        hubs[hub_id] = hub.name if hub else None
    return [
        AdminSociety.model_validate(
            {
                "id": s.id,
                "slug": s.slug,
                "name": s.name,
                "area": s.area,
                "status": s.status,
                "hub": None if s.hub_id is None else {"id": s.hub_id, "name": hubs[s.hub_id]},
                "households": counts[s.id],
                "unlock_threshold": s.unlock_threshold,
                "unlock_threshold_pinned": s.unlock_threshold_pinned,
                "threshold_source": s.threshold_source,
                "live_at": s.live_at,
                "flats": s.flats,
                "towers": s.towers,
                "boundary": None if boundary is None else GeoPolygon.model_validate_json(boundary),
                "created_at": s.created_at,
                "updated_at": s.updated_at,
            }
        )
        for s, boundary in found
    ]


async def society_out(session: AsyncSession, society: Society) -> AdminSociety:
    geojson = (
        await session.execute(
            select(func.ST_AsGeoJSON(Society.boundary, type_=Text)).where(Society.id == society.id)
        )
    ).scalar_one()
    return (await societies_out(session, [(society, geojson)]))[0]


async def create(
    session: AsyncSession, body: SocietyCreateIn, hub_id: uuid.UUID | None, actor: uuid.UUID
) -> Society:
    """A society made by hand: the shared create path (service.create_society), audit-logged."""
    society = await service.create_society(
        session,
        name=body.name,
        area=body.area,
        hub_id=hub_id,
        unlock_threshold=body.unlock_threshold,
        flats=body.flats,
        towers=[{"id": t.id or "", "label": t.label} for t in body.towers],
    )
    await audit.record(
        session,
        actor_user_id=actor,
        action="community.society_created",
        entity_type="society",
        entity_id=str(society.id),
        after={
            "name": society.name,
            "slug": society.slug,
            "hub_id": str(hub_id) if hub_id else None,
            "unlock_threshold": society.unlock_threshold,
            "status": society.status,
        },
    )
    return society


# --- society requests ----------------------------------------------------------------------------


def request_not_found() -> ProblemError:
    return ProblemError(404, "community.request_not_found", "No such society request.")


def request_closed() -> ProblemError:
    return ProblemError(409, "community.request_closed", "This request has already been reviewed.")


_LAT = func.ST_Y(cast(SocietyRequest.location, Geometry))
_LNG = func.ST_X(cast(SocietyRequest.location, Geometry))


def request_out(
    request: SocietyRequest, lat: float | None, lng: float | None
) -> SocietyRequestAdmin:
    return SocietyRequestAdmin.model_validate(
        {
            "id": request.id,
            "name": request.name,
            "area": request.area,
            "location": None if lat is None or lng is None else {"lat": lat, "lng": lng},
            "note": request.note,
            "status": request.status,
            "society_id": request.society_id if request.status == "accepted" else None,
            "duplicate_of": request.society_id if request.status == "duplicate" else None,
            "created_at": request.created_at,
            "reviewed_at": request.reviewed_at,
        }
    )


async def request_view(session: AsyncSession, request_id: uuid.UUID) -> SocietyRequestAdmin:
    row = (
        await session.execute(
            select(SocietyRequest, _LAT, _LNG).where(SocietyRequest.id == request_id)
        )
    ).one_or_none()
    if row is None:
        raise request_not_found()
    return request_out(*row)


async def list_requests(
    session: AsyncSession, *, status: str | None, cursor: str | None, limit: int
) -> tuple[list[SocietyRequestAdmin], str | None]:
    """Newest first, whoever asked: any hub manager sees the whole queue (requests have no hub)."""
    stmt = select(SocietyRequest, _LAT, _LNG)
    if status is not None:
        stmt = stmt.where(SocietyRequest.status == status)
    if cursor is not None:
        after = decode_cursor(cursor).get("after")
        try:
            assert isinstance(after, list) and len(after) == 2  # pyright: ignore[reportUnknownArgumentType]
            position = (
                datetime.fromisoformat(str(after[0])),  # pyright: ignore[reportUnknownArgumentType]
                uuid.UUID(str(after[1])),  # pyright: ignore[reportUnknownArgumentType]
            )
        except (AssertionError, ValueError, TypeError):
            raise invalid_cursor() from None
        stmt = stmt.where(tuple_(SocietyRequest.created_at, SocietyRequest.id) < tuple_(*position))
    rows = (
        await session.execute(
            stmt.order_by(SocietyRequest.created_at.desc(), SocietyRequest.id.desc()).limit(
                limit + 1
            )
        )
    ).all()
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        last = page[-1][0]
        next_cursor = encode_cursor({"after": [last.created_at.isoformat(), str(last.id)]})
    return [request_out(*row) for row in page], next_cursor


async def _locked(session: AsyncSession, request_id: uuid.UUID) -> SocietyRequest:
    request = (
        await session.execute(
            select(SocietyRequest).where(SocietyRequest.id == request_id).with_for_update()
        )
    ).scalar_one_or_none()
    if request is None:
        raise request_not_found()
    return request


async def _record(
    session: AsyncSession,
    request: SocietyRequest,
    actor: uuid.UUID,
    outcome: str,
    society_id: uuid.UUID | None,
) -> None:
    after: dict[str, Any] = {
        "outcome": outcome,
        "society_id": str(society_id) if society_id else None,
    }
    await audit.record(
        session,
        actor_user_id=actor,
        action="community.request_reviewed",
        entity_type="society_request",
        entity_id=str(request.id),
        after=after,
    )


async def accept(
    session: AsyncSession, request_id: uuid.UUID, hub_id: uuid.UUID | None, actor: uuid.UUID
) -> tuple[SocietyRequest, Society]:
    """The request becomes a `prospect` society (service.create_society). Publishes nothing."""
    request = await _locked(session, request_id)
    if request.status != "received":
        raise request_closed()
    society = await service.create_society(
        session,
        name=request.name,
        area=request.area,
        hub_id=hub_id,
        data_source=request.data_source,
    )
    request.status = "accepted"
    request.society_id = society.id
    request.reviewed_at = datetime.now(UTC)
    request.reviewed_by_user_id = actor
    await session.flush()
    await _record(session, request, actor, "accepted", society.id)
    return request, society


async def mark_duplicate(
    session: AsyncSession, request_id: uuid.UUID, society_id: uuid.UUID | None, actor: uuid.UUID
) -> SocietyRequest:
    request = await _locked(session, request_id)
    if request.status != "received":
        raise request_closed()
    if society_id is None or await session.get(Society, society_id) is None:
        raise ProblemError(
            422,
            "request.invalid",
            "Name a listed society this request repeats.",
            errors=[{"field": "society_id", "message": "A listed society is required."}],
        )
    request.status = "duplicate"
    request.society_id = society_id
    request.reviewed_at = datetime.now(UTC)
    request.reviewed_by_user_id = actor
    await session.flush()
    await _record(session, request, actor, "duplicate", society_id)
    return request


async def undo_duplicate(
    session: AsyncSession, request_id: uuid.UUID, actor: uuid.UUID
) -> SocietyRequest:
    request = await _locked(session, request_id)
    if request.status != "duplicate":
        raise request_closed()
    previous = request.society_id
    request.status = "received"
    request.society_id = None
    request.reviewed_at = None
    request.reviewed_by_user_id = None
    await session.flush()
    await _record(session, request, actor, "received", previous)
    return request
