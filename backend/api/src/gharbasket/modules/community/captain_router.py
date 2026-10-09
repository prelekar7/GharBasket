"""HTTP endpoints for Society Captains (M9 §3.2): the history, appoint and end.

Hub managers of the society's hub and admins only; a society or appointment outside the caller's
hubs is 404, as if it did not exist.
"""

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Response

from gharbasket.modules.community import captains
from gharbasket.modules.community.models import Captain, Society
from gharbasket.modules.community.schemas import CaptainEndIn, CaptainIn, CaptainOut, CaptainsOut
from gharbasket.modules.partners import api as partners_api
from gharbasket.platform.db import DbSession
from gharbasket.platform.idempotency import Idempotent
from gharbasket.platform.permissions import Permit, require

router = APIRouter()

CanRead = Annotated[Permit, require("captains:read")]
CanWrite = Annotated[Permit, require("captains:write")]


def _problem(description: str) -> dict[str, Any]:
    return {"description": description, "content": {"application/problem+json": {}}}


def _covers(permit: Permit, hub_id: uuid.UUID | None) -> bool:
    """A society without a hub is only an admin's."""
    if hub_id is None:
        return any(grant.scope_id is None for grant in permit.grants)
    return permit.allows(hub_id)


async def _society(session: DbSession, permit: Permit, society_id: uuid.UUID) -> Society:
    society = await session.get(Society, society_id)
    if society is None or not _covers(permit, society.hub_id):
        raise captains.society_not_found()
    return society


async def _view(session: DbSession, captain: Captain) -> CaptainOut:
    card = await partners_api.profile_card(session, captain.partner_id)
    return CaptainOut(
        id=captain.id,
        society_id=captain.society_id,
        hub_id=captain.hub_id,
        user_id=captain.user_id,
        partner_id=captain.partner_id,
        display_name=card.display_name if card else None,
        photo_url=card.photo_url if card else None,
        status=captain.status,  # pyright: ignore[reportArgumentType]
        since=captain.since,
        until=captain.until,
        appointed_by=captain.appointed_by,  # pyright: ignore[reportArgumentType]
        end_reason=captain.end_reason,  # pyright: ignore[reportArgumentType]
    )


@router.get(
    "/societies/{society_id}/captains",
    summary="A society's captain appointments, newest first (hub manager, admin)",
    responses={
        401: _problem("auth.invalid_token"),
        403: _problem("auth.forbidden, auth.role_not_granted"),
        404: _problem("community.society_not_found"),
    },
)
async def society_captains(
    society_id: uuid.UUID,
    permit: CanRead,
    session: DbSession,
    response: Response,
    status: Annotated[
        Literal["active", "ended"] | None, Query(description="Only this status; default all")
    ] = None,
) -> CaptainsOut:
    response.headers["Cache-Control"] = "no-store"
    society = await _society(session, permit, society_id)
    rows = await captains.history(session, society.id, status)
    return CaptainsOut(items=[await _view(session, row) for row in rows])


@router.post(
    "/societies/{society_id}/captains",
    status_code=201,
    summary="Appoint a verified captain who lives here (Idempotency-Key required)",
    responses={
        400: _problem("request.idempotency_key_required"),
        401: _problem("auth.invalid_token"),
        403: _problem("auth.forbidden, auth.role_not_granted"),
        404: _problem("community.society_not_found"),
        409: _problem("community.captain_exists (captain_id), community.already_captain"),
        422: _problem(
            "community.partner_not_eligible, community.captain_not_resident, "
            "request.idempotency_mismatch, request.invalid"
        ),
    },
)
async def appoint_captain(
    society_id: uuid.UUID,
    body: CaptainIn,
    permit: CanWrite,
    session: DbSession,
    idem: Idempotent,
) -> CaptainOut:
    society = await _society(session, permit, society_id)
    captain = await captains.appoint(
        session,
        society.id,
        body.partner_id,
        replace=body.replace,
        appointed_by="hub",
        by_user_id=permit.principal.user_id,
    )
    view = await _view(session, captain)
    await idem.save(201, view)
    await session.commit()
    return view


@router.post(
    "/captains/{captain_id}/end",
    summary="End a captain's appointment now (Idempotency-Key required)",
    responses={
        400: _problem("request.idempotency_key_required"),
        401: _problem("auth.invalid_token"),
        403: _problem("auth.forbidden, auth.role_not_granted"),
        404: _problem("community.captain_not_found"),
        409: _problem("community.captain_already_ended"),
        422: _problem("request.idempotency_mismatch, request.invalid"),
    },
)
async def end_captain(
    captain_id: uuid.UUID,
    body: CaptainEndIn,
    permit: CanWrite,
    session: DbSession,
    idem: Idempotent,
) -> CaptainOut:
    captain = await session.get(Captain, captain_id)
    if captain is None or not _covers(permit, captain.hub_id):
        raise captains.captain_not_found()
    await captains.end(
        session, captain, reason=body.reason, note=body.note, by_user_id=permit.principal.user_id
    )
    view = await _view(session, captain)
    await idem.save(200, view)
    await session.commit()
    return view
