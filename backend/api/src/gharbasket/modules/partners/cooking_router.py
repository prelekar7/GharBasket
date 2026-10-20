"""HTTP endpoints of M8 for cooking tiffins, manual assignment and the tiffin board (spec §3.4).

The kitchen starts her assignment and marks orders ready (offline-safe: each tap carries its own
Idempotency-Key); hub staff assign by hand and watch the board.
"""

import uuid
from datetime import date, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Query, Response
from sqlalchemy import select

from gharbasket.modules.community import api as community_api
from gharbasket.modules.partners import assignments, cooking, kitchens
from gharbasket.modules.partners.assignment_router import ORDER_STATUSES, views
from gharbasket.modules.partners.models import MEALS, Assignment, AssignmentOrder, AssignmentRun
from gharbasket.modules.partners.schemas import (
    AssignmentOut,
    BoardMealOut,
    BoardRunOut,
    BoardUnassignedOut,
    ManualIn,
    ManualOut,
    ReadyIn,
    SocietyRef,
    StartIn,
    TiffinBoardOut,
)
from gharbasket.platform.db import DbSession
from gharbasket.platform.idempotency import Idempotent
from gharbasket.platform.locale import Lang
from gharbasket.platform.permissions import Permit, require

router = APIRouter(tags=["partners"])

CanWrite = Annotated[Permit, require("assignments:write")]
CanManage = Annotated[Permit, require("assignments:manage")]
CanSeeBoard = Annotated[Permit, require("partners:read")]


def _problem(description: str) -> dict[str, Any]:
    return {"description": description, "content": {"application/problem+json": {}}}


COOKING: dict[int | str, dict[str, Any]] = {
    400: _problem("request.idempotency_key_required"),
    401: _problem("auth.invalid_token"),
    403: _problem("auth.forbidden, auth.role_not_granted"),
    404: _problem("partners.assignment_not_found"),
    409: _problem("partners.assignment_not_accepted, orders.illegal_transition"),
    422: _problem(
        "partners.checklist_incomplete, partners.order_not_in_assignment, "
        "request.idempotency_mismatch, request.invalid"
    ),
}


async def _saved(
    session: DbSession, idem: Idempotent, assignment: Assignment, lang: Lang
) -> AssignmentOut:
    (view,) = await views(session, [assignment], lang)
    await idem.save(200, view)
    await session.commit()
    return view


@router.post(
    "/assignments/{assignment_id}/start",
    summary="Start cooking an accepted assignment on its day: orders show Being cooked",
    responses=COOKING,
)
async def start(
    assignment_id: uuid.UUID,
    body: StartIn,
    permit: CanWrite,
    session: DbSession,
    idem: Idempotent,
    lang: Lang,
) -> AssignmentOut:
    user_id = permit.principal.user_id
    assignment = await assignments.own(session, user_id, assignment_id)
    await cooking.start(session, assignment, user_id, kitchens.now(), body.client_at)
    return await _saved(session, idem, assignment, lang)


@router.post(
    "/assignments/{assignment_id}/ready",
    summary="Mark orders ready with the hygiene checklist: orders show Ready at the kitchen",
    responses=COOKING,
)
async def ready(
    assignment_id: uuid.UUID,
    body: ReadyIn,
    permit: CanWrite,
    session: DbSession,
    idem: Idempotent,
    lang: Lang,
) -> AssignmentOut:
    user_id = permit.principal.user_id
    assignment = await assignments.own(session, user_id, assignment_id)
    await cooking.ready(
        session,
        assignment,
        user_id,
        order_ids=body.order_ids,
        checklist=body.checklist,
        at=kitchens.now(),
        client_at=body.client_at,
    )
    return await _saved(session, idem, assignment, lang)


@router.post(
    "/hubs/{hub_id}/tiffin-assignments/manual",
    summary="Assign tiffins to a kitchen by hand, accepted at once (hub manager, admin)",
    responses={
        400: _problem("request.idempotency_key_required"),
        401: _problem("auth.invalid_token"),
        403: _problem("auth.forbidden, auth.role_not_granted"),
        404: _problem("partners.kitchen_not_found"),
        409: _problem("partners.order_not_assignable, partners.assignment_closed"),
        422: _problem("request.idempotency_mismatch, request.invalid"),
    },
)
async def manual(
    hub_id: uuid.UUID,
    body: ManualIn,
    permit: CanManage,
    session: DbSession,
    idem: Idempotent,
    lang: Lang,
) -> ManualOut:
    permit.ensure(hub_id)
    assignment, over = await cooking.manual(
        session,
        hub_id=hub_id,
        day=body.date,
        order_ids=body.order_ids,
        partner_id=body.partner_id,
        note=body.note,
        by_user_id=permit.principal.user_id,
        at=kitchens.now(),
    )
    (view,) = await views(session, [assignment], lang)
    out = ManualOut(**view.model_dump(), over_capacity=over)
    await idem.save(200, out)
    await session.commit()
    return out


@router.get(
    "/hubs/{hub_id}/tiffin-board",
    summary="The day's tiffins per meal, the assignments and the runs (hub staff, admin)",
    responses={
        401: _problem("auth.invalid_token"),
        403: _problem("auth.forbidden, auth.role_not_granted"),
        422: _problem("request.invalid"),
    },
)
async def tiffin_board(
    hub_id: uuid.UUID,
    permit: CanSeeBoard,
    session: DbSession,
    response: Response,
    lang: Lang,
    on: Annotated[date | None, Query(alias="date")] = None,
) -> TiffinBoardOut:
    permit.ensure(hub_id)
    day = on or kitchens.today() + timedelta(days=1)
    statuses = tuple(s for s in ORDER_STATUSES if s not in ("approved", "payment_failed"))
    items = [
        t
        for t in await assignments.tiffins(session, hub_id, day, statuses)
        if t.order.status != "cancelled"
    ]
    pairs = (
        await session.execute(
            select(AssignmentOrder, Assignment)
            .join(Assignment, Assignment.id == AssignmentOrder.assignment_id)
            .where(Assignment.hub_id == hub_id, Assignment.on_date == day)
        )
    ).all()
    held = {row.order_id: (row, a) for row, a in pairs if row.status != "released"}
    runs = list(
        (
            await session.execute(
                select(AssignmentRun)
                .where(AssignmentRun.hub_id == hub_id, AssignmentRun.on_date == day)
                .order_by(AssignmentRun.at, AssignmentRun.id)
            )
        ).scalars()
    )
    reasons = {e["order_id"]: e["reason"] for run in runs for e in run.unassigned}
    labels = await community_api.society_labels(session, list({t.order.society_id for t in items}))
    meals = []
    for meal in MEALS:
        mine = [t for t in items if t.meal == meal]
        if not mine:
            continue
        holding = [held[t.order.id] for t in mine if t.order.id in held]
        free = [t for t in mine if t.order.id not in held and t.order.status == "locked"]
        meals.append(
            BoardMealOut(
                meal=meal,  # pyright: ignore[reportArgumentType]
                orders=len(mine),
                portions=sum(t.portions for t in mine),
                assigned=len(holding),
                accepted=sum(a.status == "accepted" for _, a in holding),
                preparing=sum(a.status == "preparing" for _, a in holding),
                ready=sum(row.status == "ready" for row, _ in holding),
                picked_up=sum(row.status == "picked_up" for row, _ in holding),
                unassigned=[
                    BoardUnassignedOut(
                        order_id=t.order.id,
                        society=SocietyRef(
                            id=t.order.society_id,
                            name=label.name if (label := labels.get(t.order.society_id)) else "",
                        ),
                        portions=t.portions,
                        reason=reasons.get(str(t.order.id), "pending"),
                    )
                    for t in free
                ],
            )
        )
    found = await session.execute(
        select(Assignment)
        .where(Assignment.hub_id == hub_id, Assignment.on_date == day)
        .order_by(Assignment.meal.desc(), Assignment.round, Assignment.id)
    )
    response.headers["Cache-Control"] = "no-store"
    return TiffinBoardOut(
        date=day,
        meals=meals,
        assignments=await views(session, list(found.scalars()), lang),
        runs=[
            BoardRunOut(
                meal=run.meal,  # pyright: ignore[reportArgumentType]
                round=run.round,
                source=run.source,  # pyright: ignore[reportArgumentType]
                model=run.model,
                status=run.status,
                gini=run.summary.get("gini"),
                km_per_portion=run.summary.get("km_per_portion"),
                at=run.at,
            )
            for run in runs
        ],
    )
