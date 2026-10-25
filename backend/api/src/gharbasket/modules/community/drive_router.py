"""HTTP endpoints for Kindness point rates and donation drives (M9 §3.4).

Everyone signed in reads the rates; customers see the running and upcoming drives of the platform,
their hub and their society. Hub managers manage their hub's drives (and see every state), admins
every drive and the rates.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Header, Path, Query, Response, status

from gharbasket.modules.community import drives, ngos
from gharbasket.modules.community.kindness_schemas import (
    DriveIn,
    DriveNgo,
    DriveOut,
    DrivePatch,
    DriveProgress,
    DrivesOut,
    DriveState,
    PointRateIn,
    PointRateOut,
    PointRatesOut,
)
from gharbasket.modules.community.models import DonationDrive, PointRate, Society
from gharbasket.modules.identity import api as identity_api
from gharbasket.platform.auth import CurrentPrincipal
from gharbasket.platform.db import DbSession
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.etag import IfMatch, set_etag
from gharbasket.platform.idempotency import Idempotent
from gharbasket.platform.locale import Lang, Language, localised
from gharbasket.platform.pagination import Cursor, Limit
from gharbasket.platform.permissions import PERMISSIONS, Permit, active_grants, require

router = APIRouter()

CanWriteRates = Annotated[Permit, require("point_rates:write")]
CanWriteDrives = Annotated[Permit, require("drives:write")]


def _problem(description: str) -> dict[str, Any]:
    return {"description": description, "content": {"application/problem+json": {}}}


def rate_out(rate: PointRate, lang: Language) -> PointRateOut:
    return PointRateOut(
        code=rate.code,
        label=localised(rate.label, lang),
        unit=rate.unit,  # pyright: ignore[reportArgumentType]
        points=rate.points,
        max_qty=float(rate.max_qty),
    )


async def _drives_out(
    session: DbSession, found: list[DonationDrive], lang: Language, *, exact: bool = True
) -> list[DriveOut]:
    counts = await drives.progress(session, [drive.id for drive in found], exact=exact)
    ngo_names = await ngos.names(session, {d.ngo_id for d in found if d.ngo_id is not None})
    on = drives.today()
    out: list[DriveOut] = []
    for drive in found:
        pledged, verified = counts[drive.id]
        out.append(
            DriveOut(
                id=drive.id,
                code=drive.code,
                name=localised(drive.name, lang),
                description=None
                if drive.description is None
                else localised(drive.description, lang),
                scope=drive.scope,  # pyright: ignore[reportArgumentType]
                hub_id=drive.hub_id,
                society_id=drive.society_id,
                codes=drive.codes,
                multiplier=float(drive.multiplier),
                starts_on=drive.starts_on,
                ends_on=drive.ends_on,
                state=drives.drive_state(drive, on),  # pyright: ignore[reportArgumentType]
                ngo=None
                if drive.ngo_id is None
                else DriveNgo(id=drive.ngo_id, name=ngo_names.get(drive.ngo_id, "")),
                goal_items=drive.goal_items,
                progress=DriveProgress(pledged=pledged, verified=verified),
                version=drive.version,
            )
        )
    return out


@router.get("/point-rates", summary="Kindness points per kg or per piece, by item category")
async def point_rates(
    _principal: CurrentPrincipal, session: DbSession, lang: Lang
) -> PointRatesOut:
    return PointRatesOut(items=[rate_out(rate, lang) for rate in await drives.rates(session)])


@router.put(
    "/admin/point-rates/{code}",
    summary="Change a point rate (admin; applies to donations verified from now on; audit-logged)",
    responses={
        403: _problem("auth.forbidden"),
        404: _problem("community.item_code_unknown"),
        422: _problem("request.invalid"),
    },
)
async def put_point_rate(
    code: Annotated[str, Path(max_length=20)],
    body: PointRateIn,
    permit: CanWriteRates,
    session: DbSession,
    lang: Lang,
) -> PointRateOut:
    rate = await drives.update_rate(session, code, body, permit.principal.user_id)
    out = rate_out(rate, lang)
    await session.commit()
    return out


async def _viewer(
    session: DbSession, principal: CurrentPrincipal, active_role: str | None
) -> drives.Viewer:
    staff_roles = PERMISSIONS.roles_for("drives:write")
    staff = [g for g in active_grants(principal, active_role) if g.role in staff_roles]
    if staff:
        return drives.Viewer(
            staff=True,
            all_hubs=any(grant.scope_id is None for grant in staff),
            hub_ids=frozenset(grant.scope_id for grant in staff if grant.scope_id is not None),
        )
    household = await identity_api.household_for_user(session, principal.user_id)
    if household is None:
        return drives.Viewer(staff=False)
    hub_id = household.hub_id
    if hub_id is None:
        society = await session.get(Society, household.society_id)
        hub_id = society.hub_id if society else None
    return drives.Viewer(staff=False, hub_id=hub_id, society_id=household.society_id)


@router.get(
    "/drives",
    summary="Donation drives: running and upcoming ones (customers), every state (hub staff)",
    responses={422: _problem("request.invalid_cursor, request.invalid")},
)
async def list_drives(
    principal: CurrentPrincipal,
    session: DbSession,
    lang: Lang,
    state: Annotated[DriveState | None, Query(description="Default: running and upcoming")] = None,
    cursor: Cursor = None,
    limit: Limit = 20,
    x_active_role: Annotated[str | None, Header(max_length=100)] = None,
) -> DrivesOut:
    viewer = await _viewer(session, principal, x_active_role)
    states = drives.OPEN_STATES if state is None else (state,)
    if not viewer.staff:
        states = tuple(s for s in states if s in drives.OPEN_STATES)
    if not states:
        return DrivesOut(items=[], next_cursor=None)
    found, next_cursor = await drives.list_drives(
        session, viewer, states, cursor=cursor, limit=limit
    )
    out = await _drives_out(session, found, lang, exact=viewer.staff)
    return DrivesOut(items=out, next_cursor=next_cursor)


EDITS = {
    403: _problem("auth.forbidden: another hub, or a platform drive (admin only)"),
    422: _problem(
        "community.item_code_unknown, community.drive_dates_invalid, "
        "community.ngo_does_not_accept, request.invalid, request.idempotency_mismatch"
    ),
}


@router.post(
    "/admin/drives",
    status_code=status.HTTP_201_CREATED,
    summary="Start a drive (hub manager: their hub or a society in it; admin: any; "
    "Idempotency-Key required)",
    responses={
        **EDITS,
        400: _problem("request.idempotency_key_required"),
        404: _problem("community.society_not_found, community.ngo_not_found"),
        409: _problem("community.drive_code_taken"),
    },
)
async def create_drive(
    body: DriveIn,
    permit: CanWriteDrives,
    session: DbSession,
    idem: Idempotent,
    lang: Lang,
    response: Response,
) -> DriveOut:
    drive = await drives.create_drive(session, body, permit)
    out = (await _drives_out(session, [drive], lang))[0]
    await idem.save(201, out)
    await session.commit()
    set_etag(response, out.version)
    return out


@router.patch(
    "/admin/drives/{drive_id}",
    summary="Change or cancel a drive (If-Match: its version; a running drive only extends)",
    responses={
        **EDITS,
        404: _problem("community.drive_not_found, community.ngo_not_found"),
        412: _problem("request.version_mismatch"),
        428: _problem("request.precondition_required"),
    },
)
async def patch_drive(
    drive_id: uuid.UUID,
    body: DrivePatch,
    permit: CanWriteDrives,
    session: DbSession,
    expected: IfMatch,
    lang: Lang,
    response: Response,
) -> DriveOut:
    drive = await drives.staff_drive(session, permit, drive_id)
    if drive.version != expected:
        raise ProblemError(
            412, "request.version_mismatch", "This changed since you loaded it; reload and retry."
        )
    await drives.patch_drive(session, drive, body, permit.principal.user_id)
    out = (await _drives_out(session, [drive], lang))[0]
    await session.commit()
    set_etag(response, out.version)
    return out
