"""HTTP endpoints of the console's societies list and society request review (spec §3.9).

Hub managers and admins list societies (`societies:read`) and review requests
(`society_requests:review`); accepting also needs `societies:write`. A society with no hub is only
an admin's. Nothing here names a captain or a requester.
"""

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Response, status

from gharbasket.modules.community import permissions as _permissions  # noqa: F401 (registers rules)
from gharbasket.modules.community import staff
from gharbasket.modules.community.schemas import (
    AdminSociety,
    SocietyRequestAccepted,
    SocietyRequestAcceptIn,
    SocietyRequestAdmin,
    SocietyRequestDuplicateIn,
    SocietyRequestUndoIn,
    SocietyStatus,
)
from gharbasket.modules.hub_ops import api as hub_ops_api
from gharbasket.platform.db import DbSession
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.idempotency import Idempotent
from gharbasket.platform.pagination import Cursor, Page
from gharbasket.platform.permissions import Permit, require

router = APIRouter(tags=["community"])

CanRead = Annotated[Permit, require("societies:read")]
CanReview = Annotated[Permit, require("society_requests:review")]
CanWrite = Annotated[Permit, require("societies:write")]
REQUESTS = "/admin/society-requests"


def _problem(description: str) -> dict[str, Any]:
    return {"description": description, "content": {"application/problem+json": {}}}


def _hubs(permit: Permit) -> list[uuid.UUID] | None:
    """The hubs the caller serves; None for an admin, who covers every hub."""
    if permit.is_global:
        return None
    return [grant.scope_id for grant in permit.grants if grant.scope_id is not None]


@router.get(
    "/admin/societies",
    summary=(
        "Every society for the console: live, unlocking, prospect, paused, then by name "
        "(hub manager: their hubs; admin: all, with hub-less ones)"
    ),
    responses={
        403: _problem("auth.forbidden (a hub_id outside the caller's grants)"),
        422: _problem("request.invalid_cursor, request.invalid"),
    },
)
async def list_admin_societies(
    permit: CanRead,
    session: DbSession,
    response: Response,
    q: Annotated[
        str | None,
        Query(max_length=80, description="Words of the name or area, as the public list"),
    ] = None,
    hub_id: Annotated[uuid.UUID | None, Query(description="One hub")] = None,
    society_status: Annotated[SocietyStatus | None, Query(alias="status")] = None,
    with_unassigned: Annotated[
        bool, Query(description="Add societies with no hub (admins only; ignored for hub staff)")
    ] = False,
    cursor: Cursor = None,
    limit: Annotated[int, Query(ge=1, le=100, description="Items per page")] = 50,
) -> Page[AdminSociety]:
    if hub_id is not None:
        permit.ensure(hub_id)
    response.headers["Cache-Control"] = "no-store"
    items, next_cursor = await staff.list_societies(
        session,
        hub_ids=_hubs(permit),
        hub_id=hub_id,
        unassigned=with_unassigned and permit.is_global,
        q=" ".join(q.split()) if q else None,
        status=society_status,
        cursor=cursor,
        limit=limit,
    )
    return Page(items=items, next_cursor=next_cursor)


@router.get(
    REQUESTS,
    summary="The society request queue, newest first (hub manager, admin)",
    responses={403: _problem("auth.forbidden"), 422: _problem("request.invalid_cursor")},
)
async def list_society_requests(
    _permit: CanReview,
    session: DbSession,
    response: Response,
    request_status: Annotated[
        Literal["received", "accepted", "duplicate", "rejected"] | None, Query(alias="status")
    ] = None,
    cursor: Cursor = None,
    limit: Annotated[int, Query(ge=1, le=100, description="Items per page")] = 50,
) -> Page[SocietyRequestAdmin]:
    response.headers["Cache-Control"] = "no-store"
    items, next_cursor = await staff.list_requests(
        session, status=request_status, cursor=cursor, limit=limit
    )
    return Page(items=items, next_cursor=next_cursor)


async def _accept_hub(
    session: DbSession, permit: Permit, requested: uuid.UUID | None
) -> uuid.UUID | None:
    """The hub the new society gets: the one named (a hub manager's own), else the caller's hub
    (a hub manager), else none (an admin)."""
    if requested is not None:
        permit.ensure(requested)
        if await hub_ops_api.get_hub(session, requested) is None:
            raise ProblemError(
                422,
                "request.invalid",
                "No such hub.",
                errors=[{"field": "hub_id", "message": "No such hub."}],
            )
        return requested
    hubs = _hubs(permit)
    if not hubs:
        return None
    if len(set(hubs)) > 1:
        raise ProblemError(
            422,
            "request.invalid",
            "Say which of your hubs the society joins.",
            errors=[{"field": "hub_id", "message": "Required when you manage several hubs."}],
        )
    return hubs[0]


@router.post(
    REQUESTS + "/{request_id}/accept",
    status_code=status.HTTP_201_CREATED,
    summary="Accept a request: it becomes a prospect society (Idempotency-Key required)",
    responses={
        403: _problem("auth.forbidden (a hub outside the caller's grants)"),
        404: _problem("community.request_not_found"),
        409: _problem("community.request_closed"),
        422: _problem("request.invalid (hub_id)"),
    },
)
async def accept_society_request(
    request_id: uuid.UUID,
    permit: CanReview,
    writer: CanWrite,
    session: DbSession,
    idem: Idempotent,
    body: SocietyRequestAcceptIn | None = None,
) -> SocietyRequestAccepted:
    hub_id = await _accept_hub(session, writer, body.hub_id if body else None)
    request, society = await staff.accept(session, request_id, hub_id, permit.principal.user_id)
    out = SocietyRequestAccepted(
        request=await staff.request_view(session, request.id),
        society=await staff.society_out(session, society),
    )
    await idem.save(201, out)
    await session.commit()
    return out


@router.post(
    REQUESTS + "/{request_id}/duplicate",
    summary="Mark a request a duplicate of a listed society (Idempotency-Key required)",
    responses={
        404: _problem("community.request_not_found"),
        409: _problem("community.request_closed"),
        422: _problem("request.invalid (errors[].field: society_id)"),
    },
)
async def duplicate_society_request(
    request_id: uuid.UUID,
    body: SocietyRequestDuplicateIn,
    permit: CanReview,
    session: DbSession,
    idem: Idempotent,
) -> SocietyRequestAdmin:
    await staff.mark_duplicate(session, request_id, body.society_id, permit.principal.user_id)
    out = await staff.request_view(session, request_id)
    await idem.save(200, out)
    await session.commit()
    return out


@router.patch(
    REQUESTS + "/{request_id}",
    summary='Undo "Duplicate": the request goes back to received',
    responses={
        404: _problem("community.request_not_found"),
        409: _problem("community.request_closed (not a duplicate)"),
        422: _problem('request.invalid (any body other than {"status": "received"})'),
    },
)
async def undo_duplicate_request(
    request_id: uuid.UUID, body: SocietyRequestUndoIn, permit: CanReview, session: DbSession
) -> SocietyRequestAdmin:
    del body  # the model only admits {"status": "received"}
    await staff.undo_duplicate(session, request_id, permit.principal.user_id)
    out = await staff.request_view(session, request_id)
    await session.commit()
    return out
