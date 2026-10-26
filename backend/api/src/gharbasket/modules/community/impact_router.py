"""HTTP endpoint of customers' impact (M9 §3.7): `GET /v1/impact?scope=me|society`. The hub, NGO and
platform views are M12's (`/v1/analytics/impact`)."""

import uuid
from dataclasses import asdict
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Response

from gharbasket.modules.community import impact
from gharbasket.modules.community.impact_schemas import (
    DonatedLineOut,
    HubImpactOut,
    MyImpactOut,
    SocietyImpactOut,
)
from gharbasket.modules.community.models import Society
from gharbasket.modules.identity import api as identity_api
from gharbasket.platform.db import DbSession
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.permissions import Permit, forbidden, require

router = APIRouter()

STAFF = ("hub_manager", "franchise_owner")  # hub staff, by the hub they hold


def _problem(description: str) -> dict[str, Any]:
    return {"description": description, "content": {"application/problem+json": {}}}


async def _may_read(permit: Permit, session: DbSession, society: Society) -> bool:
    """A resident (acting as a customer), the society's captain, the staff of its hub, admin."""
    if permit.is_global:
        return True
    for grant in permit.grants:
        if grant.scope_id is None:
            continue
        if grant.role in STAFF and grant.scope_id == society.hub_id:
            return True
        if grant.role == "captain" and grant.scope_id == society.id:
            return True
    if permit.has_role("customer"):
        household = await identity_api.household_for_user(session, permit.principal.user_id)
        return household is not None and household.society_id == society.id
    return False


@router.get(
    "/impact",
    summary="What a household (`me`) or a society (`society`) has given; small numbers are null",
    responses={
        403: _problem(
            "auth.forbidden: `me` needs the customer role; `society` is for its residents, its "
            "captain, its hub's staff and admins"
        ),
        404: _problem("community.society_not_found"),
        422: _problem("request.invalid: `society_id` missing, or a `period` other than `all`"),
    },
)
async def get_impact(
    permit: Annotated[Permit, require("impact:read")],
    session: DbSession,
    response: Response,
    scope: Annotated[Literal["me", "society"], Query(description="Whose numbers")],
    society_id: Annotated[uuid.UUID | None, Query(description="For `society`")] = None,
    period: Annotated[
        str,
        Query(
            max_length=20,
            description="`society` only: `all`. A society's numbers are all time only; anything "
            "else is 422 `request.invalid`",
            examples=["all"],
        ),
    ] = "all",
) -> MyImpactOut | SocietyImpactOut:
    if scope == "me":
        if not permit.has_role("customer"):
            raise forbidden("Your own impact is for customers.")
        response.headers["Cache-Control"] = "no-store"
        mine = await impact.my_impact(session, permit.principal.user_id)
        return MyImpactOut(
            items_donated=mine.items_donated,
            kg_donated=mine.kg_donated,
            donations_verified=mine.donations_verified,
            points_earned=mine.points_earned,
            meals_donated=mine.meals_donated,
            homes_invited=mine.homes_invited,
            donated=[DonatedLineOut.model_validate(asdict(line)) for line in mine.donated],
        )
    if society_id is None:
        raise ProblemError(422, "request.invalid", "Give the `society_id` of the society.")
    impact.check_period(period)
    society = await session.get(Society, society_id)
    if society is None:
        raise ProblemError(404, "community.society_not_found", "No such society.")
    if not await _may_read(permit, session, society):
        raise forbidden()
    found = await impact.society_impact(session, society_id, period)
    assert found is not None  # the society was just read
    response.headers["Cache-Control"] = "private, max-age=60"
    return SocietyImpactOut(
        society_id=found.society_id,
        as_of=found.as_of,
        homes_joined=found.homes_joined,
        items_donated=found.items_donated,
        kg_donated=found.kg_donated,
        meals_donated=found.meals_donated,
        contributors=found.contributors,
        hub=None if found.hub is None else HubImpactOut.model_validate(asdict(found.hub)),
    )
