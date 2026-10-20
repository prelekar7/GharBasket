"""HTTP endpoints of M8 for tiffin assignments (spec §3.4).

A kitchen partner reads her offers and accepts or declines them; hub staff run a round now.
"""

import uuid
from collections import defaultdict
from datetime import date, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import select

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.community import api as community_api
from gharbasket.modules.partners import assignments, kitchens, pickups
from gharbasket.modules.partners.models import Assignment, AssignmentOrder, Partner
from gharbasket.modules.partners.schemas import (
    AssignedPartnerOut,
    AssignmentOrderOut,
    AssignmentOut,
    AssignmentsOut,
    DeclineIn,
    DishOut,
    RunIn,
    RunOut,
    SlotOut,
    SocietyRef,
)
from gharbasket.platform.db import DbSession
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.idempotency import Idempotent
from gharbasket.platform.locale import Lang, Language, localised
from gharbasket.platform.permissions import Permit, require
from gharbasket.platform.settings import Settings, get_settings

router = APIRouter(tags=["partners"])

CanRead = Annotated[Permit, require("assignments:read")]
CanWrite = Annotated[Permit, require("assignments:write")]
CanManage = Annotated[Permit, require("assignments:manage")]
AppSettings = Annotated[Settings, Depends(get_settings)]
# Every order status, so a view still shows orders that moved on.
ORDER_STATUSES = (
    "approved",
    "payment_failed",
    "locked",
    "picking",
    "packed",
    "dispatched",
    "delivered",
    "undelivered",
    "refunded",
    "cancelled",
)
HISTORY_DAYS = 7


def _problem(description: str) -> dict[str, Any]:
    return {"description": description, "content": {"application/problem+json": {}}}


def hhmm(value: Any) -> str:
    return value.strftime("%H:%M")


async def views(session: DbSession, items: list[Assignment], lang: Language) -> list[AssignmentOut]:
    """Assignments as the kitchen and hub staff see them: short codes, dishes in `lang` and
    society names; never a flat or a customer's name."""
    if not items:
        return []
    rows = await session.execute(
        select(AssignmentOrder).where(AssignmentOrder.assignment_id.in_([a.id for a in items]))
    )
    lines: dict[uuid.UUID, list[AssignmentOrder]] = defaultdict(list)
    for row in rows.scalars():
        lines[row.assignment_id].append(row)
    tiffins: dict[uuid.UUID, assignments.Tiffin] = {}
    for hub_id, day in {(a.hub_id, a.on_date) for a in items}:
        for item in await assignments.tiffins(session, hub_id, day, ORDER_STATUSES):
            tiffins[item.order.id] = item
    names = await catalog_api.line_products(session, list({t.product_id for t in tiffins.values()}))
    societies = await community_api.society_labels(
        session, list({t.order.society_id for t in tiffins.values()})
    )
    partners = {a.partner_id: await session.get(Partner, a.partner_id) for a in items}
    out = []
    for assignment in items:
        orders, dishes = [], defaultdict(int)
        for row in sorted(lines[assignment.id], key=lambda r: r.short_code):
            tiffin = tiffins.get(row.order_id)
            if tiffin is None:
                continue
            product = names.get(tiffin.product_id)
            label = societies.get(tiffin.order.society_id)
            orders.append(
                AssignmentOrderOut(
                    order_id=row.order_id,
                    short_code=row.short_code,
                    portions=row.portions,
                    product=localised(product.names, lang) if product else "",
                    society=SocietyRef(
                        id=tiffin.order.society_id, name=label.name if label else ""
                    ),
                    status=row.status,  # pyright: ignore[reportArgumentType]
                )
            )
            if row.status != "released":
                dishes[tiffin.product_id] += row.portions
        slot = next((t.slot for t in tiffins.values() if t.slot.id == assignment.slot_id), None)
        partner = partners[assignment.partner_id]
        out.append(
            AssignmentOut(
                id=assignment.id,
                date=assignment.on_date,
                meal=assignment.meal,  # pyright: ignore[reportArgumentType]
                hub_id=assignment.hub_id,
                slot=SlotOut(
                    id=assignment.slot_id,
                    label=slot.label if slot else "",
                    start=hhmm(slot.start) if slot else "",
                    end=hhmm(slot.end) if slot else "",
                ),
                partner=AssignedPartnerOut(
                    id=assignment.partner_id, display_name=partner.display_name if partner else ""
                ),
                status=assignment.status,  # pyright: ignore[reportArgumentType]
                round=assignment.round,
                source=assignment.source,  # pyright: ignore[reportArgumentType]
                portions=assignment.portions,
                dishes=[
                    DishOut(
                        product_id=pid,
                        name=localised(names[pid].names, lang) if pid in names else "",
                        portions=n,
                    )
                    for pid, n in sorted(dishes.items(), key=lambda kv: str(kv[0]))
                ],
                orders=orders,
                respond_by=assignment.respond_by,
                ready_by=hhmm(assignments.ready_by(slot.start)) if slot else "",
                pickup_qr=pickups.pickup_qr(assignment.id)
                if any(o.status != "assigned" and o.status != "released" for o in orders)
                else None,
                checklist=assignment.checklist,
                accepted_at=assignment.responded_at
                if assignment.status not in ("offered", "declined", "expired")
                else None,
                ready_at=assignment.ready_at,
                version=assignment.version,
            )
        )
    return out


@router.get(
    "/assignments",
    summary="Your kitchen's assignments for a date (default tomorrow; a week back to tomorrow)",
    responses={
        401: _problem("auth.invalid_token"),
        403: _problem("auth.forbidden, auth.role_not_granted"),
        404: _problem("partners.kitchen_not_found"),
        422: _problem("request.invalid"),
    },
)
async def my_assignments(
    permit: CanRead,
    session: DbSession,
    response: Response,
    lang: Lang,
    on: Annotated[date | None, Query(alias="date")] = None,
) -> AssignmentsOut:
    partner, _ = await kitchens.own(session, permit.principal.user_id)
    tomorrow = kitchens.today() + timedelta(days=1)
    day = on or tomorrow
    if not tomorrow - timedelta(days=HISTORY_DAYS + 1) <= day <= tomorrow:
        raise ProblemError(422, "request.invalid", "Ask for a date from a week ago to tomorrow.")
    rows = await session.execute(
        select(Assignment)
        .where(Assignment.partner_id == partner.id, Assignment.on_date == day)
        .order_by(Assignment.meal.desc(), Assignment.round, Assignment.id)
    )
    response.headers["Cache-Control"] = "no-store"
    return AssignmentsOut(items=await views(session, list(rows.scalars()), lang))


ANSWER_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: _problem("request.idempotency_key_required"),
    401: _problem("auth.invalid_token"),
    403: _problem("auth.forbidden, auth.role_not_granted"),
    404: _problem("partners.assignment_not_found"),
    409: _problem("partners.assignment_closed"),
    422: _problem("request.idempotency_mismatch, request.invalid"),
}


async def _answered(
    session: DbSession, idem: Idempotent, assignment: Assignment, lang: Language
) -> AssignmentOut:
    (view,) = await views(session, [assignment], lang)
    await idem.save(200, view)
    await session.commit()
    return view


@router.post(
    "/assignments/{assignment_id}/accept",
    summary="Accept the whole offer while it is open (Idempotency-Key required)",
    responses=ANSWER_RESPONSES,
)
async def accept(
    assignment_id: uuid.UUID, permit: CanWrite, session: DbSession, idem: Idempotent, lang: Lang
) -> AssignmentOut:
    assignment = await assignments.own(session, permit.principal.user_id, assignment_id)
    await assignments.accept(session, assignment, permit.principal.user_id, kitchens.now())
    return await _answered(session, idem, assignment, lang)


@router.post(
    "/assignments/{assignment_id}/decline",
    summary="Decline the whole offer while it is open; its orders go to the next round",
    responses=ANSWER_RESPONSES,
)
async def decline(
    assignment_id: uuid.UUID,
    body: DeclineIn,
    permit: CanWrite,
    session: DbSession,
    idem: Idempotent,
    lang: Lang,
) -> AssignmentOut:
    assignment = await assignments.own(session, permit.principal.user_id, assignment_id)
    await assignments.decline(
        session, assignment, body.reason, body.note, permit.principal.user_id, kitchens.now()
    )
    return await _answered(session, idem, assignment, lang)


@router.post(
    "/hubs/{hub_id}/tiffin-assignments/run",
    summary="Run one assignment round now for a date (hub manager, admin; Idempotency-Key)",
    responses={
        400: _problem("request.idempotency_key_required"),
        401: _problem("auth.invalid_token"),
        403: _problem("auth.forbidden, auth.role_not_granted"),
        422: _problem("request.idempotency_mismatch, request.invalid"),
    },
)
async def run_round(
    hub_id: uuid.UUID,
    body: RunIn,
    permit: CanManage,
    session: DbSession,
    idem: Idempotent,
    request: Request,
    settings: AppSettings,
) -> RunOut:
    permit.ensure(hub_id)
    result = await assignments.run_round(
        session,
        getattr(request.app.state, "http", None),
        settings,
        hub_id,
        body.date,
        at=kitchens.now(),
    )
    out = RunOut(
        run_id=result.run_ids[0] if result.run_ids else None,
        run_ids=result.run_ids,
        offers=result.offers,
        unassigned=result.unassigned,
    )
    await idem.save(200, out)
    await session.commit()
    return out
