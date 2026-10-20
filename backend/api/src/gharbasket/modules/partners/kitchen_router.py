"""HTTP endpoints of M8 for kitchens and their capacity (spec §3.4).

A kitchen partner reads and edits her own kitchen and capacity; hub staff of her hub (and admins)
record an inspection's hygiene grade.
"""

import uuid
from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Query, Response

from gharbasket.modules.partners import kitchens
from gharbasket.modules.partners.models import Kitchen, Partner
from gharbasket.modules.partners.schemas import (
    CapacitiesOut,
    CapacityIn,
    CapacityOut,
    HygieneOut,
    InspectionIn,
    KitchenOut,
    KitchenPatchIn,
    WeekPortions,
)
from gharbasket.platform.db import DbSession
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.etag import IfMatch, set_etag
from gharbasket.platform.geo import LatLng
from gharbasket.platform.idempotency import Idempotent
from gharbasket.platform.permissions import Permit, require

router = APIRouter(tags=["partners"])

CanWrite = Annotated[Permit, require("kitchens:write")]
CanInspect = Annotated[Permit, require("kitchens:inspect")]


def _problem(description: str) -> dict[str, Any]:
    return {"description": description, "content": {"application/problem+json": {}}}


OWN: dict[int | str, dict[str, Any]] = {
    401: _problem("auth.invalid_token"),
    403: _problem("auth.forbidden, auth.role_not_granted"),
    404: _problem("partners.kitchen_not_found"),
}


def kitchen_out(partner: Partner, kitchen: Kitchen) -> KitchenOut:
    hygiene = None
    if kitchen.hygiene_grade is not None and kitchen.inspected_on is not None:
        hygiene = HygieneOut(
            grade=kitchen.hygiene_grade,  # pyright: ignore[reportArgumentType]
            inspected_on=kitchen.inspected_on,
        )
    location = None
    if kitchen.lat is not None and kitchen.lng is not None:
        location = LatLng(lat=kitchen.lat, lng=kitchen.lng)
    return KitchenOut(
        partner_id=partner.id,
        display_name=partner.display_name,
        status=partner.status,  # pyright: ignore[reportArgumentType]
        area=kitchen.area,
        address=kitchen.address,
        location=location,
        fssai_reg_no=kitchen.fssai_reg_no,
        fssai_valid_until=kitchen.fssai_valid_until,
        skills=list(kitchen.skills),
        default_portions=WeekPortions(**kitchen.default_portions),
        hygiene=hygiene,
        version=kitchen.version,
    )


async def _own(session: DbSession, permit: Permit) -> tuple[Partner, Kitchen]:
    partner, kitchen = await kitchens.own(session, permit.principal.user_id)
    if not permit.allows(partner.hub_id):
        raise kitchens.not_found()
    return partner, kitchen


@router.get("/kitchens/me", summary="Your kitchen (kitchen partner)", responses=OWN)
async def my_kitchen(permit: CanWrite, session: DbSession, response: Response) -> KitchenOut:
    partner, kitchen = await _own(session, permit)
    response.headers["Cache-Control"] = "no-store"
    set_etag(response, kitchen.version)
    return kitchen_out(partner, kitchen)


@router.patch(
    "/kitchens/me",
    summary="Edit your kitchen: name, area, address, pin, skills, weekly portions (If-Match)",
    responses={
        **OWN,
        412: _problem("request.version_mismatch: reload and try again"),
        422: _problem("partners.skills_invalid, partners.capacity_invalid, request.invalid"),
    },
)
async def edit_kitchen(
    body: KitchenPatchIn,
    permit: CanWrite,
    session: DbSession,
    expected: IfMatch,
    response: Response,
) -> KitchenOut:
    partner, kitchen = await _own(session, permit)
    if kitchen.version != expected:
        raise ProblemError(412, "request.version_mismatch", "The kitchen changed; reload it.")
    changes = body.model_dump(exclude_unset=True)
    if changes.get("display_name", "") is None:
        changes.pop("display_name")
    await kitchens.edit(session, partner, kitchen, changes)
    out = kitchen_out(partner, kitchen)
    await session.commit()
    set_etag(response, out.version)
    return out


@router.post(
    "/kitchens/{partner_id}/inspection",
    summary="Record a kitchen inspection's hygiene grade (hub staff, admin; audit-logged)",
    responses={
        400: _problem("request.idempotency_key_required"),
        401: _problem("auth.invalid_token"),
        403: _problem("auth.forbidden, auth.role_not_granted"),
        404: _problem("partners.kitchen_not_found"),
        422: _problem("request.idempotency_mismatch, request.invalid"),
    },
)
async def inspect_kitchen(
    partner_id: uuid.UUID,
    body: InspectionIn,
    permit: CanInspect,
    session: DbSession,
    idem: Idempotent,
) -> KitchenOut:
    partner, kitchen = await kitchens.of_partner(session, partner_id)
    if not permit.allows(partner.hub_id):
        raise kitchens.not_found()
    await kitchens.inspect(
        session, kitchen, grade=body.grade, note=body.note, by_user_id=permit.principal.user_id
    )
    out = kitchen_out(partner, kitchen)
    await idem.save(200, out)
    await session.commit()
    return out


def _capacity_out(capacity: kitchens.Capacity, assigned: int) -> CapacityOut:
    return CapacityOut(
        date=capacity.on_date,
        meal=capacity.meal,  # pyright: ignore[reportArgumentType]
        max_portions=capacity.max_portions,
        source=capacity.source,  # pyright: ignore[reportArgumentType]
        assigned_portions=assigned,
        locked=capacity.locked,
    )


@router.get(
    "/kitchen-capacity",
    summary="Your capacity per date and meal: from today, up to 14 days (default 7)",
    responses={**OWN, 422: _problem("partners.capacity_invalid, request.invalid")},
)
async def my_capacity(
    permit: CanWrite,
    session: DbSession,
    response: Response,
    start: Annotated[date | None, Query(alias="from")] = None,
    end: Annotated[date | None, Query(alias="to")] = None,
) -> CapacitiesOut:
    _, kitchen = await _own(session, permit)
    first, last = kitchens.window(start, end)
    assigned = await kitchens.assigned_portions(session, kitchen.partner_id, first, last)
    response.headers["Cache-Control"] = "no-store"
    return CapacitiesOut(
        items=[
            _capacity_out(c, assigned.get((c.on_date, c.meal), 0))
            for c in await kitchens.capacities(session, kitchen, first, last)
        ]
    )


@router.put(
    "/kitchen-capacity",
    summary="Set your capacity for one date and meal (0 = none) until 21:00 the day before",
    responses={
        **OWN,
        409: _problem("partners.capacity_locked"),
        422: _problem("partners.capacity_invalid, request.invalid"),
    },
)
async def set_capacity(body: CapacityIn, permit: CanWrite, session: DbSession) -> CapacityOut:
    _, kitchen = await _own(session, permit)
    capacity = await kitchens.set_capacity(
        session, kitchen, body.date, body.meal, body.max_portions
    )
    assigned = await kitchens.assigned_portions(session, kitchen.partner_id, body.date, body.date)
    await session.commit()
    return _capacity_out(capacity, assigned.get((body.date, body.meal), 0))
