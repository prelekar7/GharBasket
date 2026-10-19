"""Cooking, manual assignment, cancellation and refunds of tiffins nobody takes (spec §3.4, §6).

The kitchen starts an accepted assignment from T 00:00 (its orders move `locked → picking`,
actor `kitchen`) and marks orders ready with the hygiene checklist (`picking → packed`, after
`locked → picking` when she skipped start). M4 checks the state machine: an order the hub already
moved answers 409 `orders.illegal_transition` and the request changes nothing.

A hub manager may assign by hand (`accepted`, `source: manual`, over capacity if need be) until
the meal's `ready_by` minus 2 hours. After that, a locked tiffin no assignment holds is refunded
in full by `refund_unassigned_tiffins` through M4 (`locked → refunded`, actor `system`, reason
`no_kitchen`, ADR-0027).
"""

import uuid
from datetime import date, datetime, time, timedelta

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.partners import assignments, kitchens, matching
from gharbasket.modules.partners.models import (
    Assignment,
    AssignmentOrder,
    AssignmentRun,
    Kitchen,
    Partner,
)
from gharbasket.platform import audit
from gharbasket.platform.errors import ProblemError

log = structlog.get_logger("gharbasket.partners")
IST = assignments.IST
CHECKLIST = ("handwash", "hair_cover", "clean_surface", "sealed", "fresh_today")
MANUAL_UNTIL = timedelta(hours=2)  # before the meal's ready_by


def _not_accepted(detail: str) -> ProblemError:
    return ProblemError(409, "partners.assignment_not_accepted", detail)


def day_starts(day: date) -> datetime:
    return datetime.combine(day, time(0), IST)


async def ready_by_at(session: AsyncSession, assignment: Assignment) -> datetime | None:
    slot = await orders_api.slot_ref(session, assignment.slot_id)
    if slot is None:
        return None
    return datetime.combine(assignment.on_date, assignments.ready_by(slot.start), IST)


async def _held_rows(session: AsyncSession, assignment: Assignment) -> list[AssignmentOrder]:
    rows = await session.execute(
        select(AssignmentOrder)
        .where(AssignmentOrder.assignment_id == assignment.id)
        .order_by(AssignmentOrder.short_code)
    )
    return list(rows.scalars())


async def start(
    session: AsyncSession,
    assignment: Assignment,
    user_id: uuid.UUID,
    at: datetime,
    client_at: datetime | None,
) -> None:
    """From `accepted`, on the day: every assigned order moves to `picking` ("Being cooked")."""
    if assignment.status != "accepted" or at < day_starts(assignment.on_date):
        raise _not_accepted("Start an accepted assignment on its day.")
    for row in await _held_rows(session, assignment):
        if row.status == "assigned":
            await orders_api.transition(
                session, row.order_id, "picking", actor="kitchen", actor_user_id=user_id
            )
    assignment.status, assignment.started_at = "preparing", at
    await assignments.event(session, assignment, "started", user_id, client_at=client_at)


async def ready(
    session: AsyncSession,
    assignment: Assignment,
    user_id: uuid.UUID,
    *,
    order_ids: list[uuid.UUID] | None,
    checklist: dict[str, bool],
    at: datetime,
    client_at: datetime | None,
) -> None:
    """Mark orders ready (default: every assigned one) with all five checklist items true; the
    assignment is ready when no order is left to cook. Late is allowed and recorded."""
    if any(checklist.get(item) is not True for item in CHECKLIST):
        raise ProblemError(
            422,
            "partners.checklist_incomplete",
            "Tick every hygiene item first.",
            extra={"items": list(CHECKLIST)},
        )
    if assignment.status not in ("accepted", "preparing") or at < day_starts(assignment.on_date):
        raise _not_accepted("Accept the assignment and cook it on its day.")
    rows = await _held_rows(session, assignment)
    cooking = {row.order_id: row for row in rows if row.status == "assigned"}
    targets = list(cooking) if order_ids is None else order_ids
    unknown = [str(oid) for oid in targets if oid not in cooking]
    if unknown or not targets:
        raise ProblemError(
            422,
            "partners.order_not_in_assignment",
            "These orders are not waiting in this assignment.",
            extra={"order_ids": unknown},
        )
    for order_id in targets:
        order = await orders_api.get(session, order_id)
        if order.status == "locked":
            await orders_api.transition(
                session, order_id, "picking", actor="kitchen", actor_user_id=user_id
            )
        await orders_api.transition(
            session, order_id, "packed", actor="kitchen", actor_user_id=user_id
        )
        cooking[order_id].status = "ready"
    assignment.checklist = {item: True for item in CHECKLIST}
    if assignment.status == "accepted":
        assignment.status, assignment.started_at = "preparing", at
    late = False
    if all(row.status != "assigned" for row in rows):
        due = await ready_by_at(session, assignment)
        late = due is not None and at > due
        assignment.status, assignment.ready_at, assignment.late = "ready", at, late
    await assignments.event(
        session,
        assignment,
        "ready",
        user_id,
        order_ids=[str(oid) for oid in targets],
        late=late,
        client_at=client_at,
    )


async def manual(
    session: AsyncSession,
    *,
    hub_id: uuid.UUID,
    day: date,
    order_ids: list[uuid.UUID],
    partner_id: uuid.UUID,
    note: str | None,
    by_user_id: uuid.UUID,
    at: datetime,
) -> tuple[Assignment, bool]:
    """An accepted assignment the hub manager agreed with the kitchen by phone (audit-logged);
    it may exceed her capacity. Returns it and whether it is over capacity."""
    partner = await session.get(Partner, partner_id)
    kitchen = await session.get(Kitchen, partner_id)
    if (
        partner is None
        or kitchen is None
        or partner.hub_id != hub_id
        or partner.status not in ("active", "paused")
    ):
        raise kitchens.not_found()
    free = {t.order.id: t for t in await assignments.tiffins(session, hub_id, day)}
    taken = await assignments.held(session, list(free))
    items = [free[oid] for oid in dict.fromkeys(order_ids) if oid in free and oid not in taken]
    meal = items[0].meal if items else None
    bad = [str(oid) for oid in dict.fromkeys(order_ids) if oid not in {t.order.id for t in items}]
    bad += [str(t.order.id) for t in items if t.meal != meal]
    if bad or not items:
        raise ProblemError(
            409,
            "partners.order_not_assignable",
            "Only free locked tiffins of this hub, date and one meal can be assigned.",
            extra={"order_ids": bad},
        )
    first = min(items, key=lambda t: t.slot.start)
    closes = datetime.combine(day, assignments.ready_by(first.slot.start), IST) - MANUAL_UNTIL
    if at >= closes:
        raise ProblemError(
            409, "partners.assignment_closed", "Manual assignment closed 2 hours before ready_by."
        )
    assert meal is not None
    overrides = await kitchens.overrides(session, [partner_id], day, day)
    capacity = overrides.get((partner_id, day, meal), kitchens.default_for(kitchen, day, meal))
    live = await assignments.live_portions(session, [partner_id], day, day)
    portions = sum(t.portions for t in items)
    over = portions > capacity - live.get((partner_id, day, meal), 0)
    last_round = await session.scalar(
        select(AssignmentRun.round)
        .where(AssignmentRun.hub_id == hub_id, AssignmentRun.on_date == day)
        .order_by(AssignmentRun.round.desc())
        .limit(1)
    )

    def km(item: assignments.Tiffin) -> float | None:
        if item.point is None or kitchen is None or kitchen.lat is None or kitchen.lng is None:
            return None
        return round(matching.km(kitchen.lat, kitchen.lng, *item.point), 2)

    assignment = await assignments.create(
        session,
        partner=partner,
        items=[(item, km(item)) for item in items],
        round_no=last_round or 1,
        run_id=None,
        source="manual",
        status="accepted",
        respond_until=at,
        by_user_id=by_user_id,
    )
    await audit.record(
        session,
        actor_user_id=by_user_id,
        action="partners.manual_assignment",
        entity_type="assignment",
        entity_id=str(assignment.id),
        after={
            "partner_id": str(partner_id),
            "order_ids": [str(t.order.id) for t in items],
            "portions": portions,
            "over_capacity": over,
            "note": note,
        },
    )
    return assignment, over


async def cancel_for_partner(session: AsyncSession, partner_id: uuid.UUID, from_day: date) -> int:
    """A suspended (or leaving) kitchen's offered and accepted assignments from `from_day` are
    cancelled and their orders released; preparing and ready ones stay with the hub."""
    found = (
        (
            await session.execute(
                select(Assignment)
                .where(
                    Assignment.partner_id == partner_id,
                    Assignment.on_date >= from_day,
                    Assignment.status.in_(("offered", "accepted")),
                )
                .order_by(Assignment.id)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    for assignment in found:
        accepted = assignment.status == "accepted"
        assignment.status = "cancelled"
        await assignments.event(session, assignment, "cancelled", None)
        await assignments.release(session, assignment, "cancelled", clear_kitchen=accepted)
    return len(found)


async def refund_unassigned(
    session: AsyncSession, hub_id: uuid.UUID, day: date, at: datetime
) -> tuple[int, int]:
    """Locked tiffins of `day` that no assignment holds once manual assignment has closed for
    their meal are refunded in full through M4 (once: a refunded order is no longer locked).
    Returns (refunded, refused by M4)."""
    items = await assignments.tiffins(session, hub_id, day)
    taken = await assignments.held(session, [t.order.id for t in items])
    refunded = refused = 0
    for item in items:
        closes = datetime.combine(day, assignments.ready_by(item.slot.start), IST) - MANUAL_UNTIL
        if item.order.id in taken or at < closes:
            continue
        try:
            async with session.begin_nested():
                await orders_api.transition(
                    session,
                    item.order.id,
                    "refunded",
                    actor="system",
                    payload={"reason": "no_kitchen"},
                )
            refunded += 1
        except ProblemError as exc:
            refused += 1
            log.warning("tiffin_refund_refused", order_id=str(item.order.id), code=exc.code)
    return refunded, refused
