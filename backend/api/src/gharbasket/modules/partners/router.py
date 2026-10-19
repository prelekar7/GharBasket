"""HTTP endpoints of M8 Partner Network: the partner core (spec §3.3).

A partner's own records (`/partners/me`, pause and resume), and hub staff's list, profile, suspend
and reinstate. Applications and training (§3.1, §3.2) are in `application_router`, kitchens and
capacity (§3.4) in `kitchen_router`, tiffin assignments in `assignment_router` and
`cooking_router` (jobs in `jobs`); ratings and earnings arrive later in S6.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, Response

from gharbasket.modules.community import api as community_api
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.partners import applications, service
from gharbasket.modules.partners import jobs as _jobs  # noqa: F401 (registers the jobs)
from gharbasket.modules.partners import permissions as _permissions  # noqa: F401 (registers rules)
from gharbasket.modules.partners.application_router import router as application_router
from gharbasket.modules.partners.assignment_router import router as assignment_router
from gharbasket.modules.partners.cooking_router import router as cooking_router
from gharbasket.modules.partners.kitchen_router import router as kitchen_router
from gharbasket.modules.partners.kyc_router import router as kyc_router
from gharbasket.modules.partners.models import Partner
from gharbasket.modules.partners.rate_router import router as rate_router
from gharbasket.modules.partners.schemas import (
    PartnerKind,
    PartnerOut,
    PartnerPatchIn,
    PartnersOut,
    PartnerStaffOut,
    PartnerStatus,
    RatingOut,
    ReinstateIn,
    SocietyRef,
    SuspendIn,
    SuspensionOut,
)
from gharbasket.platform.auth import CurrentPrincipal
from gharbasket.platform.db import DbSession
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.etag import IfMatch, set_etag
from gharbasket.platform.idempotency import Idempotent
from gharbasket.platform.pagination import Cursor, Limit, Page
from gharbasket.platform.permissions import Permit, require

router = APIRouter(tags=["partners"])
router.include_router(application_router)
router.include_router(kyc_router)
router.include_router(kitchen_router)
router.include_router(assignment_router)
router.include_router(cooking_router)
router.include_router(rate_router)

CanRead = Annotated[Permit, require("partners:read")]
CanSuspend = Annotated[Permit, require("partners:suspend")]
# Ratings are shown from this many on (§3.5).
RATING_SHOWN_FROM = 5


def _problem(description: str) -> dict[str, Any]:
    return {"description": description, "content": {"application/problem+json": {}}}


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


def _out(partner: Partner, *, staff: bool = False) -> dict[str, Any]:
    rating = None
    if partner.rating_avg is not None and partner.rating_count >= RATING_SHOWN_FROM:
        rating = RatingOut(average=float(partner.rating_avg), count=partner.rating_count)
    suspension = None
    if partner.suspended_at is not None and partner.suspend_reason_code is not None:
        suspension = SuspensionOut(
            at=partner.suspended_at,
            reason_code=partner.suspend_reason_code,  # pyright: ignore[reportArgumentType]
            note=partner.suspend_note if staff else None,
        )
    return {
        "id": partner.id,
        "user_id": partner.user_id,
        "kind": partner.kind,
        "hub_id": partner.hub_id,
        "display_name": partner.display_name,
        "photo_url": None,
        "status": partner.status,
        "rating": rating,
        "verified_at": partner.verified_at,
        "suspension": suspension,
        "version": partner.version,
    }


async def _society(session: DbSession, society_id: uuid.UUID | None) -> SocietyRef | None:
    """A captain's requested society (from her application); seeded partners have none."""
    if society_id is None:
        return None
    found = await community_api.get_society(session, society_id)
    return SocietyRef(id=society_id, name=found.name if found else "")


async def _staff_views(session: DbSession, partners: list[Partner]) -> list[PartnerStaffOut]:
    contacts = await identity_api.user_contacts(session, [p.user_id for p in partners])
    societies = await applications.requested_societies(
        session, [p.application_id for p in partners if p.application_id is not None]
    )
    views = []
    for partner in partners:
        contact = contacts.get(partner.user_id)
        views.append(
            PartnerStaffOut(
                **_out(partner, staff=True),
                name=contact.name if contact else None,
                phone_e164=contact.phone_e164 if contact else None,
                application_id=partner.application_id,
                requested_society=await _society(session, societies.get(partner.application_id))
                if partner.application_id is not None
                else None,
            )
        )
    return views


async def _staff_partner(session: DbSession, permit: Permit, partner_id: uuid.UUID) -> Partner:
    """The partner, if she is in one of the caller's hubs (admins: any); else 404."""
    partner = await service.get(session, partner_id)
    if not permit.allows(partner.hub_id):
        raise service.not_found()
    return partner


async def _own_partner(
    session: DbSession, user_id: uuid.UUID, partner_id: uuid.UUID, *, for_update: bool = False
) -> Partner:
    partner = await service.get(session, partner_id, for_update=for_update)
    if partner.user_id != user_id:
        raise service.not_found()
    return partner


@router.get(
    "/partners/me",
    summary="Your partner records, one per kind",
    responses={401: _problem("auth.invalid_token")},
)
async def my_partners(
    principal: CurrentPrincipal, session: DbSession, response: Response
) -> PartnersOut:
    _no_store(response)
    partners = await service.for_user(session, principal.user_id)
    return PartnersOut(items=[PartnerOut(**_out(p)) for p in partners])


@router.get(
    "/hubs/{hub_id}/partners",
    summary="The hub's partners by name (hub staff, admin)",
    responses={
        401: _problem("auth.invalid_token"),
        403: _problem("auth.forbidden, auth.role_not_granted"),
        422: _problem("request.invalid_cursor, request.invalid"),
    },
)
async def hub_partners(
    hub_id: uuid.UUID,
    permit: CanRead,
    session: DbSession,
    response: Response,
    kind: PartnerKind | None = None,
    status: Annotated[PartnerStatus | None, Query()] = None,
    cursor: Cursor = None,
    limit: Limit = 20,
) -> Page[PartnerStaffOut]:
    permit.ensure(hub_id)
    _no_store(response)
    partners, next_cursor = await service.list_for_hub(
        session, hub_id, kind=kind, status=status, cursor=cursor, limit=limit
    )
    return Page(items=await _staff_views(session, partners), next_cursor=next_cursor)


@router.get(
    "/partners/{partner_id}",
    summary="A partner's profile (hub staff of her hub, admin)",
    responses={
        401: _problem("auth.invalid_token"),
        403: _problem("auth.forbidden, auth.role_not_granted"),
        404: _problem("partners.partner_not_found"),
    },
)
async def get_partner(
    partner_id: uuid.UUID, permit: CanRead, session: DbSession, response: Response
) -> PartnerStaffOut:
    _no_store(response)
    partner = await _staff_partner(session, permit, partner_id)
    (view,) = await _staff_views(session, [partner])
    return view


@router.patch(
    # The uuid converter keeps `PATCH /partners/me` a 405: `me` is the read-only route above.
    "/partners/{partner_id:uuid}",
    summary="Change your display name (If-Match)",
    responses={
        401: _problem("auth.invalid_token"),
        404: _problem("partners.partner_not_found"),
        405: _problem("http.method_not_allowed: the path is not a partner id (PATCH /partners/me)"),
        412: _problem("request.version_mismatch: reload and try again"),
        422: _problem("partners.display_name_invalid, request.invalid"),
        428: _problem("request.precondition_required"),
    },
)
async def edit_partner(
    partner_id: uuid.UUID,
    body: PartnerPatchIn,
    principal: CurrentPrincipal,
    session: DbSession,
    expected: IfMatch,
    response: Response,
) -> PartnerOut:
    # Row-locked, so the If-Match check and the write are one step.
    partner = await _own_partner(session, principal.user_id, partner_id, for_update=True)
    if partner.version != expected:
        raise ProblemError(412, "request.version_mismatch", "The record changed; reload it.")
    await service.rename(session, partner, body.display_name)
    out = PartnerOut(**_out(partner))
    await session.commit()
    set_etag(response, out.version)
    return out


@router.post(
    "/partners/{partner_id}/suspend",
    summary="Suspend a partner at once (Idempotency-Key required; audit-logged)",
    responses={
        400: _problem("request.idempotency_key_required"),
        401: _problem("auth.invalid_token"),
        403: _problem("auth.forbidden, auth.role_not_granted"),
        404: _problem("partners.partner_not_found"),
        409: _problem("partners.already_suspended"),
        422: _problem("request.idempotency_mismatch, request.invalid"),
    },
)
async def suspend_partner(
    partner_id: uuid.UUID,
    body: SuspendIn,
    permit: CanSuspend,
    session: DbSession,
    idem: Idempotent,
) -> PartnerStaffOut:
    partner = await _staff_partner(session, permit, partner_id)
    await service.suspend(
        session,
        partner,
        reason_code=body.reason_code,
        note=body.note,
        by_user_id=permit.principal.user_id,
    )
    (view,) = await _staff_views(session, [partner])
    await idem.save(200, view)
    await session.commit()
    return view


@router.post(
    "/partners/{partner_id}/reinstate",
    summary="Make a suspended partner active again (Idempotency-Key required; audit-logged)",
    responses={
        400: _problem("request.idempotency_key_required"),
        401: _problem("auth.invalid_token"),
        403: _problem("auth.forbidden, auth.role_not_granted"),
        404: _problem("partners.partner_not_found"),
        409: _problem("partners.not_suspended"),
        422: _problem("request.idempotency_mismatch, request.invalid"),
    },
)
async def reinstate_partner(
    partner_id: uuid.UUID,
    body: ReinstateIn,
    permit: CanSuspend,
    session: DbSession,
    idem: Idempotent,
) -> PartnerStaffOut:
    partner = await _staff_partner(session, permit, partner_id)
    await service.reinstate(session, partner, note=body.note, by_user_id=permit.principal.user_id)
    (view,) = await _staff_views(session, [partner])
    await idem.save(200, view)
    await session.commit()
    return view


async def _pause(
    partner_id: uuid.UUID,
    principal: CurrentPrincipal,
    session: DbSession,
    idem: Idempotent,
    *,
    paused: bool,
) -> PartnerOut:
    partner = await _own_partner(session, principal.user_id, partner_id)
    await service.set_paused(session, partner, paused=paused)
    view = PartnerOut(**_out(partner))
    await idem.save(200, view)
    await session.commit()
    return view


PAUSE_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: _problem("request.idempotency_key_required"),
    401: _problem("auth.invalid_token"),
    404: _problem("partners.partner_not_found"),
    409: _problem("partners.pause_not_allowed"),
    422: _problem("request.idempotency_mismatch, request.invalid"),
}


@router.post(
    "/partners/{partner_id}/pause",
    summary="Take a break: no new offers (kitchens, riders, packers; Idempotency-Key required)",
    responses=PAUSE_RESPONSES,
)
async def pause_partner(
    partner_id: uuid.UUID, principal: CurrentPrincipal, session: DbSession, idem: Idempotent
) -> PartnerOut:
    return await _pause(partner_id, principal, session, idem, paused=True)


@router.post(
    "/partners/{partner_id}/resume",
    summary="Come back from a break (Idempotency-Key required)",
    responses=PAUSE_RESPONSES,
)
async def resume_partner(
    partner_id: uuid.UUID, principal: CurrentPrincipal, session: DbSession, idem: Idempotent
) -> PartnerOut:
    return await _pause(partner_id, principal, session, idem, paused=False)
