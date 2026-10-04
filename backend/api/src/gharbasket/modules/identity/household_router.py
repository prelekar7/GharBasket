"""HTTP endpoints for households and consents (M1 spec §3.2), included by `identity.router`."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Response, status

from gharbasket.modules.identity import households
from gharbasket.modules.identity.schemas import (
    ConsentIn,
    ConsentOut,
    HouseholdIn,
    HouseholdOut,
    HouseholdUpdate,
)
from gharbasket.platform.auth import CurrentPrincipal
from gharbasket.platform.db import DbSession
from gharbasket.platform.etag import IfMatch, set_etag
from gharbasket.platform.idempotency import Idempotent
from gharbasket.platform.permissions import Permit, require

router = APIRouter(tags=["households"])

CanRead = Annotated[Permit, require("households:read")]
CanWrite = Annotated[Permit, require("households:write")]


def _problem(description: str) -> dict[str, Any]:
    return {"description": description, "content": {"application/problem+json": {}}}


_ETAG = {"ETag": {"description": 'The household version, e.g. "3"; send it back in If-Match'}}


@router.post(
    "/households",
    status_code=status.HTTP_201_CREATED,
    summary="Onboarding: household, members, address and consents in one call",
    description="One transaction behind one Idempotency-Key: a retry never duplicates anything.",
    responses={
        201: {"headers": _ETAG},
        400: _problem("request.idempotency_key_required"),
        401: _problem("auth.invalid_token"),
        403: _problem("identity.health_consent_required, auth.forbidden"),
        409: _problem(
            "identity.household_exists (with household_id), identity.consent_version_stale "
            "(with current_version)"
        ),
        422: _problem(
            "identity.society_unknown, identity.tower_unknown, identity.address_outside_society, "
            "identity.diet_conflict, identity.consent_required, request.idempotency_mismatch, "
            "request.invalid"
        ),
    },
)
async def create_household(
    body: HouseholdIn,
    permit: CanWrite,
    session: DbSession,
    idem: Idempotent,
    response: Response,
) -> HouseholdOut:
    household = await households.create_household(session, permit.principal.user_id, body)
    created = await households.view(session, household)
    await idem.save(201, created)
    await session.commit()
    set_etag(response, created.version)
    return created


@router.get(
    "/households/me",
    summary="The signed-in user's household",
    responses={
        200: {"headers": _ETAG},
        401: _problem("auth.invalid_token"),
        404: _problem("identity.household_not_found: not onboarded yet"),
    },
)
async def my_household(permit: CanRead, session: DbSession, response: Response) -> HouseholdOut:
    household = await households.household_of(session, permit.principal.user_id)
    if household is None:
        raise households.household_not_found()
    found = await households.view(session, household)
    set_etag(response, found.version)
    return found


@router.get(
    "/households/{household_id}",
    summary="A household (owner only)",
    responses={
        200: {"headers": _ETAG},
        401: _problem("auth.invalid_token"),
        404: _problem("identity.household_not_found"),
    },
)
async def get_household(
    household_id: uuid.UUID, permit: CanRead, session: DbSession, response: Response
) -> HouseholdOut:
    household = await households.owned(session, permit.principal.user_id, household_id)
    found = await households.view(session, household)
    set_etag(response, found.version)
    return found


@router.patch(
    "/households/{household_id}",
    summary="Change household settings (If-Match required; only the fields sent)",
    responses={
        200: {"headers": _ETAG},
        401: _problem("auth.invalid_token"),
        404: _problem("identity.household_not_found"),
        412: _problem("identity.version_conflict: reload and try again"),
        422: _problem("identity.diet_conflict, request.invalid"),
        428: _problem("request.precondition_required: send If-Match"),
    },
)
async def update_household(
    household_id: uuid.UUID,
    body: HouseholdUpdate,
    permit: CanWrite,
    expected: IfMatch,
    session: DbSession,
    response: Response,
) -> HouseholdOut:
    household = await households.owned(
        session, permit.principal.user_id, household_id, for_update=True
    )
    household = await households.update_household(session, household, expected, body)
    updated = await households.view(session, household)
    await session.commit()
    set_etag(response, updated.version)
    return updated


@router.get(
    "/consents",
    summary="Every consent purpose, given or not",
    responses={401: _problem("auth.invalid_token")},
)
async def my_consents(principal: CurrentPrincipal, session: DbSession) -> list[ConsentOut]:
    return await households.consents_view(session, principal.user_id)


@router.post(
    "/consents",
    summary="Give or withdraw one consent (withdrawing health erases health details)",
    responses={
        401: _problem("auth.invalid_token"),
        409: _problem("identity.consent_version_stale (with current_version)"),
        422: _problem("request.invalid"),
    },
)
async def set_consent(
    body: ConsentIn, principal: CurrentPrincipal, session: DbSession
) -> ConsentOut:
    households.check_consent_version(body.version)
    if body.granted:
        await households.grant(session, principal.user_id, body.purpose)
    else:
        await households.revoke(session, principal.user_id, body.purpose)
    await session.commit()
    current = await households.consents_view(session, principal.user_id)
    return next(consent for consent in current if consent.purpose == body.purpose)
