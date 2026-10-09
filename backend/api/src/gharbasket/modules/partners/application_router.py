"""HTTP endpoints of M8 for applications (spec §3.1).

Applicants apply, follow and withdraw their own application; hub staff of the application's hub
(and admins) read the review queue and create rider and packer applications for people without a
household.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Response

from gharbasket.modules.community import api as community_api
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.partners import applications
from gharbasket.modules.partners.models import Application
from gharbasket.modules.partners.schemas import (
    ApplicantOut,
    ApplicationIn,
    ApplicationOut,
    ApplicationReviewOut,
    ApplicationSocietyOut,
    ApplicationStatus,
    HubApplicationIn,
    PartnerKind,
)
from gharbasket.platform.auth import CurrentPrincipal
from gharbasket.platform.db import DbSession
from gharbasket.platform.idempotency import Idempotent
from gharbasket.platform.pagination import Cursor, Limit, Page
from gharbasket.platform.permissions import PERMISSIONS, Permit, active_grants, require

router = APIRouter(tags=["partners"])

REVIEW = "partner_applications:review"
CanReview = Annotated[Permit, require(REVIEW)]


def _problem(description: str) -> dict[str, Any]:
    return {"description": description, "content": {"application/problem+json": {}}}


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


async def _reviewer(
    principal: CurrentPrincipal,
    x_active_role: Annotated[str | None, Header(max_length=100)] = None,
) -> Permit | None:
    """Hub staff who may review applications, or None for anyone else."""
    roles = PERMISSIONS.roles_for(REVIEW)
    grants = tuple(g for g in active_grants(principal, x_active_role) if g.role in roles)
    return Permit(principal, REVIEW, grants) if grants else None


async def _outs(session: DbSession, items: list[Application]) -> list[ApplicationOut]:
    views = await applications.views(session, items)
    return [_out(application, views[application.id]) for application in items]


def _out(application: Application, view: applications.View) -> ApplicationOut:
    return ApplicationOut(
        id=application.id,
        kind=application.kind,  # pyright: ignore[reportArgumentType]
        status=application.status,  # pyright: ignore[reportArgumentType]
        scope_type=application.scope_type,  # pyright: ignore[reportArgumentType]
        scope_id=application.scope_id,
        hub_id=application.hub_id,
        display_name=application.display_name,
        requirements={  # pyright: ignore[reportArgumentType]
            "documents": view.documents,
            "trainings": view.trainings,
        },
        next_step=view.next_step,  # pyright: ignore[reportArgumentType]
        reason=application.reason,
        partner_id=application.partner_id,
        submitted_at=application.created_at,
        decided_at=application.decided_at,
        verified_at=application.verified_at,
        version=application.version,
    )


async def _reviews(session: DbSession, items: list[Application]) -> list[ApplicationReviewOut]:
    outs = await _outs(session, items)
    contacts = await identity_api.user_contacts(session, [a.user_id for a in items])
    reviews = []
    for application, out in zip(items, outs, strict=True):
        contact = contacts.get(application.user_id)
        society = None
        if application.scope_type == "society":
            found = await community_api.get_society(session, application.scope_id)
            society = ApplicationSocietyOut(
                id=application.scope_id,
                name=found.name if found else None,
                # M9's captain_for_society joins with the captains PR.
                has_active_captain=None,
            )
        reviews.append(
            ApplicationReviewOut(
                **out.model_dump(),
                applicant=ApplicantOut(
                    user_id=application.user_id,
                    name=contact.name if contact else None,
                    phone_e164=contact.phone_e164 if contact else None,
                ),
                society=society,
            )
        )
    return reviews


async def _own(session: DbSession, user_id: uuid.UUID, application_id: uuid.UUID) -> Application:
    application = await applications.get(session, application_id)
    if application.user_id != user_id:
        raise applications.not_found()
    return application


APPLY_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: _problem("request.idempotency_key_required"),
    401: _problem("auth.invalid_token"),
    409: _problem("partners.already_applied (with application_id or partner_id)"),
    422: _problem(
        "partners.kind_invalid, partners.household_required, partners.no_hub, "
        "partners.captain_not_resident, partners.scope_invalid, partners.display_name_invalid, "
        "request.idempotency_mismatch, request.invalid"
    ),
}


@router.post(
    "/partner-applications",
    status_code=201,
    summary="Apply to become a partner (Idempotency-Key required)",
    responses=APPLY_RESPONSES,
)
async def apply(
    body: ApplicationIn, principal: CurrentPrincipal, session: DbSession, idem: Idempotent
) -> ApplicationOut:
    application = await applications.apply(
        session,
        principal.user_id,
        kind=body.kind,
        scope_id=body.scope_id,
        display_name=body.display_name,
    )
    (out,) = await _outs(session, [application])
    await idem.save(201, out)
    await session.commit()
    return out


@router.post(
    "/hubs/{hub_id}/partner-applications",
    status_code=201,
    summary="Create a rider or packer application for a person without a household "
    "(hub staff, admin; Idempotency-Key required; audit-logged)",
    responses={
        400: _problem("request.idempotency_key_required"),
        401: _problem("auth.invalid_token"),
        403: _problem("auth.forbidden, auth.role_not_granted"),
        409: _problem("partners.already_applied (with application_id or partner_id)"),
        422: _problem(
            "partners.kind_invalid, partners.user_not_found, partners.display_name_invalid, "
            "request.idempotency_mismatch, request.invalid"
        ),
    },
)
async def create_for_person(
    hub_id: uuid.UUID,
    body: HubApplicationIn,
    permit: CanReview,
    session: DbSession,
    idem: Idempotent,
) -> ApplicationOut:
    permit.ensure(hub_id)
    application = await applications.create_for(
        session,
        hub_id=hub_id,
        kind=body.kind,
        phone_e164=body.phone_e164,
        display_name=body.display_name,
        by_user_id=permit.principal.user_id,
    )
    (out,) = await _outs(session, [application])
    await idem.save(201, out)
    await session.commit()
    return out


@router.get(
    "/partner-applications/me",
    summary="Your open application, else your latest",
    responses={
        401: _problem("auth.invalid_token"),
        404: _problem("partners.application_not_found"),
    },
)
async def my_application(
    principal: CurrentPrincipal, session: DbSession, response: Response
) -> ApplicationOut:
    _no_store(response)
    application = await applications.current_for(session, principal.user_id)
    if application is None:
        raise applications.not_found()
    (out,) = await _outs(session, [application])
    return out


@router.get(
    "/partner-applications/{application_id}",
    summary="An application: the applicant's own, or the review view for hub staff of its hub",
    responses={
        401: _problem("auth.invalid_token"),
        403: _problem("auth.role_not_granted"),
        404: _problem("partners.application_not_found"),
    },
)
async def get_application(
    application_id: uuid.UUID,
    principal: CurrentPrincipal,
    reviewer: Annotated[Permit | None, Depends(_reviewer)],
    session: DbSession,
    response: Response,
) -> ApplicationReviewOut | ApplicationOut:
    _no_store(response)
    application = await applications.get(session, application_id)
    if application.user_id == principal.user_id:
        (out,) = await _outs(session, [application])
        return out
    if reviewer is None or not reviewer.allows(application.hub_id):
        raise applications.not_found()
    (review,) = await _reviews(session, [application])
    return review


@router.post(
    "/partner-applications/{application_id}/withdraw",
    summary="Withdraw your application before verification (Idempotency-Key required)",
    responses={
        400: _problem("request.idempotency_key_required"),
        401: _problem("auth.invalid_token"),
        404: _problem("partners.application_not_found"),
        409: _problem("partners.application_closed"),
        422: _problem("request.idempotency_mismatch, request.invalid"),
    },
)
async def withdraw(
    application_id: uuid.UUID, principal: CurrentPrincipal, session: DbSession, idem: Idempotent
) -> ApplicationOut:
    application = await _own(session, principal.user_id, application_id)
    await applications.withdraw(session, application)
    (out,) = await _outs(session, [application])
    await idem.save(200, out)
    await session.commit()
    return out


@router.get(
    "/hubs/{hub_id}/partner-applications",
    summary="The hub's review queue, oldest submitted first (hub staff, admin)",
    responses={
        401: _problem("auth.invalid_token"),
        403: _problem("auth.forbidden, auth.role_not_granted"),
        422: _problem("request.invalid_cursor, request.invalid"),
    },
)
async def review_queue(
    hub_id: uuid.UUID,
    permit: CanReview,
    session: DbSession,
    response: Response,
    status: Annotated[ApplicationStatus, Query()] = "kyc_pending",
    kind: PartnerKind | None = None,
    cursor: Cursor = None,
    limit: Limit = 20,
) -> Page[ApplicationReviewOut]:
    permit.ensure(hub_id)
    _no_store(response)
    items, next_cursor = await applications.queue(
        session, hub_id, status=status, kind=kind, cursor=cursor, limit=limit
    )
    return Page(items=await _reviews(session, items), next_cursor=next_cursor)
