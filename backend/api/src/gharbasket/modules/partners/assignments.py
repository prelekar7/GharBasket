"""Tiffin assignment rounds and offers (spec §3.4, §6).

A round takes a hub's locked tiffin orders for a date that no live assignment holds, per meal:
the preferred cook first (ADR-0027), then one request to M10's fair assignment through
`intel_gateway.api.assign_kitchens`, or M8's greedy (`matching.greedy`) when the gateway does not
answer, so ML never blocks the evening. Orders are grouped into one offer per kitchen; each offer
publishes `partners.assignment_created`, the run is kept in `assignment_runs`, and orders nobody
could take are published once as `partners.tiffins_unassigned`. Kitchens that declined or let an
offer expire for the date and meal are left out of the later rounds.

A kitchen accepts or declines an offer while it is open; declined and expired offers release
their orders (`partners.assignment_released`) for the next round.
"""

import time as clock
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

import httpx2
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.community import api as community_api
from gharbasket.modules.intel_gateway import api as intel_api
from gharbasket.modules.intel_gateway.api import AssignRequest
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.partners import events, kitchens, matching
from gharbasket.modules.partners.models import (
    LIVE,
    SKILLS,
    Assignment,
    AssignmentEvent,
    AssignmentOrder,
    AssignmentRun,
    Kitchen,
    Partner,
)
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.jobs import lock_key
from gharbasket.platform.outbox import publish
from gharbasket.platform.settings import Settings

IST = kitchens.IST
LUNCH_BEFORE = time(16, 0)  # a tiffin slot starting before 16:00 is lunch
FIRST_REPLY = time(22, 30)  # round 1 offers are open until 22:30 IST the evening before
LATER_REPLY = timedelta(minutes=30)
READY_BEFORE_SLOT = timedelta(minutes=60)
RATED_FROM = 5
TIME_LIMIT_S = 5.0  # the solver's budget (M10 §3.5)
LEFT_OUT = ("declined", "expired")  # kitchens left out of the next rounds for that date and meal


def meal_of(start: time) -> str:
    return "lunch" if start < LUNCH_BEFORE else "dinner"


def ready_by(start: time) -> time:
    return (datetime.combine(date.min, start) - READY_BEFORE_SLOT).time()


@dataclass(frozen=True)
class Tiffin:
    """A tiffin order as a round and a view need it."""

    order: orders_api.Order
    portions: int
    product_id: uuid.UUID
    skills: frozenset[str]
    slot: orders_api.SlotRef
    meal: str
    point: tuple[float, float] | None  # the society point; None: no location


async def tiffins(
    session: AsyncSession, hub_id: uuid.UUID, day: date, statuses: tuple[str, ...] = ("locked",)
) -> list[Tiffin]:
    found = await orders_api.orders_for_delivery(session, hub_id, day, statuses, ("tiffin",))
    found = [s for s in found if s.lines]
    products = await catalog_api.line_products(
        session, list({line.product_id for s in found for line in s.lines})
    )
    slots = {
        sid: await orders_api.slot_ref(session, sid) for sid in {s.order.slot_id for s in found}
    }
    points = await community_api.society_points(session, list({s.order.society_id for s in found}))
    items = []
    for summary in found:
        slot = slots[summary.order.slot_id]
        if slot is None:
            continue
        tags = set().union(
            *(products[ln.product_id].tags for ln in summary.lines if ln.product_id in products)
        )
        items.append(
            Tiffin(
                summary.order,
                sum(line.qty for line in summary.lines),
                summary.lines[0].product_id,
                frozenset(tags) & frozenset(SKILLS),
                slot,
                meal_of(slot.start),
                points.get(summary.order.society_id),
            )
        )
    return items


async def held(session: AsyncSession, order_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    """Orders some assignment still holds (not released)."""
    rows = await session.execute(
        select(AssignmentOrder.order_id).where(
            AssignmentOrder.order_id.in_(order_ids), AssignmentOrder.status != "released"
        )
    )
    return set(rows.scalars())


async def live_portions(
    session: AsyncSession, partner_ids: list[uuid.UUID], first: date, last: date
) -> dict[tuple[uuid.UUID, date, str], int]:
    rows = await session.execute(
        select(
            Assignment.partner_id,
            Assignment.on_date,
            Assignment.meal,
            func.sum(Assignment.portions),
        )
        .where(
            Assignment.partner_id.in_(partner_ids),
            Assignment.on_date.between(first, last),
            Assignment.status.in_(LIVE),
        )
        .group_by(Assignment.partner_id, Assignment.on_date, Assignment.meal)
    )
    return {(p, d, m): int(n) for p, d, m, n in rows.all()}


async def cooks(
    session: AsyncSession, hub_id: uuid.UUID, day: date, meal: str
) -> list[matching.Cook]:
    """The hub's active kitchens with a location and capacity left, less those that declined or
    let an offer expire for this date and meal."""
    rows = (
        await session.execute(
            select(Partner, Kitchen)
            .join(Kitchen, Kitchen.partner_id == Partner.id)
            .where(
                Partner.hub_id == hub_id,
                Partner.kind == "kitchen",
                Partner.status == "active",
                Kitchen.location.is_not(None),
            )
        )
    ).all()
    out = await session.execute(
        select(Assignment.partner_id).where(
            Assignment.hub_id == hub_id,
            Assignment.on_date == day,
            Assignment.meal == meal,
            Assignment.status.in_(LEFT_OUT),
        )
    )
    left_out = set(out.scalars())
    ids = [partner.id for partner, _ in rows if partner.id not in left_out]
    overrides = await kitchens.overrides(session, ids, day, day)
    live = await live_portions(session, ids, day, day)
    found = []
    for partner, kitchen in rows:
        if partner.id in left_out or kitchen.lat is None or kitchen.lng is None:
            continue
        capacity = overrides.get((partner.id, day, meal), kitchens.default_for(kitchen, day, meal))
        left = capacity - live.get((partner.id, day, meal), 0)
        rated = partner.rating_avg is not None and partner.rating_count >= RATED_FROM
        if left > 0:
            found.append(
                matching.Cook(
                    partner.id,
                    kitchen.lat,
                    kitchen.lng,
                    left,
                    frozenset(kitchen.skills),
                    float(partner.rating_avg) if rated and partner.rating_avg else matching.UNRATED,
                    0,  # rolling earnings: billing.api.partner_rolling_earnings is not built yet
                )
            )
    return found


def respond_by(round_no: int, day: date, at: datetime) -> datetime:
    """Round 1: 22:30 IST the evening before (later if the round itself is late); later rounds:
    30 minutes after the offer."""
    if round_no == 1:
        return max(datetime.combine(day - timedelta(days=1), FIRST_REPLY, IST), at + LATER_REPLY)
    return at + LATER_REPLY


@dataclass
class RoundResult:
    run_ids: list[uuid.UUID]
    offers: int
    unassigned: int


async def run_round(
    session: AsyncSession,
    http: httpx2.AsyncClient | None,
    settings: Settings,
    hub_id: uuid.UUID,
    day: date,
    *,
    at: datetime,
) -> RoundResult:
    """One round for both meals of `day` at a hub; only orders no assignment holds take part,
    so running it again changes nothing for orders already offered."""
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key(f"partners.assign:{hub_id}")}
    )
    items = await tiffins(session, hub_id, day)
    taken = await held(session, [t.order.id for t in items])
    by_meal: dict[str, list[Tiffin]] = defaultdict(list)
    for item in items:
        if item.order.id not in taken:
            by_meal[item.meal].append(item)
    result = RoundResult([], 0, 0)
    for meal in sorted(by_meal):
        run, offers, missed = await _meal_round(
            session, http, settings, hub_id, day, meal, by_meal[meal], at
        )
        result.run_ids.append(run.id)
        result.offers, result.unassigned = result.offers + offers, result.unassigned + missed
    return result


async def _solve(
    http: httpx2.AsyncClient | None,
    settings: Settings,
    request: AssignRequest,
    session: AsyncSession,
    hub_id: uuid.UUID,
    day: date,
) -> intel_api.KitchenAssignment | None:
    """M10's assignment, or None (M8's greedy answers): the gateway times out by itself."""
    if http is not None:
        return await intel_api.assign_kitchens(
            http, settings, request, session=session, hub_id=hub_id, for_date=day
        )
    async with httpx2.AsyncClient(timeout=httpx2.Timeout(5.0)) as client:
        return await intel_api.assign_kitchens(
            client, settings, request, session=session, hub_id=hub_id, for_date=day
        )


async def _meal_round(
    session: AsyncSession,
    http: httpx2.AsyncClient | None,
    settings: Settings,
    hub_id: uuid.UUID,
    day: date,
    meal: str,
    items: list[Tiffin],
    at: datetime,
) -> tuple[AssignmentRun, int, int]:
    started = clock.monotonic()
    last = await session.scalar(
        select(func.max(AssignmentRun.round)).where(
            AssignmentRun.hub_id == hub_id, AssignmentRun.on_date == day, AssignmentRun.meal == meal
        )
    )
    round_no = (last or 0) + 1
    unassigned = {t.order.id: "no_location" for t in items if t.point is None}
    orders = [
        matching.Order(t.order.id, t.point[0], t.point[1], t.portions, t.skills)
        for t in items
        if t.point is not None
    ]
    available = await cooks(session, hub_id, day, meal)
    matches, rest = matching.preferred_first(orders, available)
    plan = None
    if rest and available:
        request = AssignRequest.model_validate(
            {
                "orders": [
                    {"id": str(o.id), "lat": o.lat, "lng": o.lng, "portions": o.portions}
                    | {"skills": sorted(o.skills)}
                    for o in rest
                ],
                "kitchens": [
                    {"id": str(c.id), "lat": c.lat, "lng": c.lng, "max_portions": c.left}
                    | {"skills": sorted(c.skills), "rating": c.rating, "earnings_paise": c.earnings}
                    for c in available
                ],
                "fairness_lambda": settings.partners_fairness_lambda,
                "rating_floor": matching.RATING_FLOOR,
                "max_km": matching.MAX_KM,
                "paise_per_portion": matching.PAISE_PER_PORTION,
                "time_limit_s": TIME_LIMIT_S,
            }
        )
        plan = await _solve(http, settings, request, session, hub_id, day)
    if plan is not None:
        source, model, status = "ml", plan.model, plan.status
        matches += [
            matching.Match(uuid.UUID(a.order_id), uuid.UUID(a.kitchen_id), a.portions, a.km)
            for a in plan.assignments
        ]
        unassigned |= {uuid.UUID(u.order_id): u.reason for u in plan.unassigned}
        summary = plan.summary.model_dump(exclude={"earnings_paise"}) if plan.summary else {}
    else:
        source, model, status = "fallback", "greedy-m8", "fallback"
        more, missed = matching.greedy(rest, available)
        matches += more
        unassigned |= missed
        summary = matching.summary(matches, available)
    run = AssignmentRun(
        hub_id=hub_id,
        on_date=day,
        meal=meal,
        round=round_no,
        source=source,
        model=model[:40],
        status=status,
        portions=sum(m.portions for m in matches),
        unassigned=[
            {"order_id": str(k), "reason": v} for k, v in sorted(unassigned.items(), key=str)
        ],
        summary=summary,
        seconds=Decimal(str(round(clock.monotonic() - started, 2))),
    )
    session.add(run)
    await session.flush()
    offers = await _offer(session, run, matches, {t.order.id: t for t in items}, at)
    if unassigned:
        await publish(
            session,
            events.TIFFINS_UNASSIGNED,
            {
                "hub_id": str(hub_id),
                "date": day.isoformat(),
                "meal": meal,
                "run_id": str(run.id),
                "order_ids": [entry["order_id"] for entry in run.unassigned],
                "reasons": [entry["reason"] for entry in run.unassigned],
            },
        )
    return run, offers, len(unassigned)


async def next_short_code(session: AsyncSession, hub_id: uuid.UUID, day: date, meal: str) -> int:
    codes = await session.execute(
        select(AssignmentOrder.short_code)
        .join(Assignment, Assignment.id == AssignmentOrder.assignment_id)
        .where(Assignment.hub_id == hub_id, Assignment.on_date == day, Assignment.meal == meal)
    )
    return max((int(code[2:]) for code in codes.scalars()), default=0) + 1


async def create(
    session: AsyncSession,
    *,
    partner: Partner,
    items: list[tuple[Tiffin, float | None]],
    round_no: int,
    run_id: uuid.UUID | None,
    source: str,
    status: str,
    respond_until: datetime,
    by_user_id: uuid.UUID | None = None,
) -> Assignment:
    """One offer (or a manual, accepted assignment) of these orders to one kitchen; their box
    labels are kept from an earlier offer, else the next free `T-nn`."""
    first = min((t for t, _ in items), key=lambda t: (t.slot.start, str(t.slot.id)))
    day, meal = first.order.delivery_date, first.meal
    assignment = Assignment(
        partner_id=partner.id,
        hub_id=partner.hub_id,
        on_date=day,
        meal=meal,
        slot_id=first.slot.id,
        round=round_no,
        run_id=run_id,
        source=source,
        status=status,
        portions=sum(t.portions for t, _ in items),
        respond_by=respond_until,
        responded_at=respond_until if status == "accepted" else None,
        version=1,
    )
    session.add(assignment)
    await session.flush()
    old = await session.execute(
        select(AssignmentOrder.order_id, AssignmentOrder.short_code).where(
            AssignmentOrder.order_id.in_([t.order.id for t, _ in items])
        )
    )
    codes = dict(old.all())
    number = await next_short_code(session, partner.hub_id, day, meal)
    for item, distance in sorted(items, key=lambda pair: str(pair[0].order.id)):
        code = codes.get(item.order.id)
        if code is None:
            code, number = f"T-{number:02d}", number + 1
        session.add(
            AssignmentOrder(
                assignment_id=assignment.id,
                order_id=item.order.id,
                portions=item.portions,
                short_code=code,
                status="assigned",
                km=None if distance is None else Decimal(str(distance)),
            )
        )
    session.add(AssignmentEvent(assignment_id=assignment.id, action=status, user_id=by_user_id))
    await session.flush()
    await publish(
        session,
        events.ASSIGNMENT_CREATED,
        {
            "assignment_id": str(assignment.id),
            "partner_id": str(partner.id),
            "user_id": str(partner.user_id),
            "hub_id": str(partner.hub_id),
            "date": day.isoformat(),
            "meal": meal,
            "portions": assignment.portions,
            "orders_count": len(items),
            "respond_by": respond_until.isoformat(),
            "round": round_no,
        },
    )
    return assignment


async def _offer(
    session: AsyncSession,
    run: AssignmentRun,
    matches: list[matching.Match],
    items: dict[uuid.UUID, Tiffin],
    at: datetime,
) -> int:
    by_partner: dict[uuid.UUID, list[tuple[Tiffin, float | None]]] = defaultdict(list)
    for match in matches:
        by_partner[match.partner_id].append((items[match.order_id], match.km))
    for partner_id in sorted(by_partner, key=str):
        partner = await session.get(Partner, partner_id)
        if partner is None:
            continue
        await create(
            session,
            partner=partner,
            items=by_partner[partner_id],
            round_no=run.round,
            run_id=run.id,
            source=run.source,
            status="offered",
            respond_until=respond_by(run.round, run.on_date, at),
        )
    return len(by_partner)


# --- the kitchen's answer, expiry and release ---------------------------------------------------


def not_found() -> ProblemError:
    return ProblemError(404, "partners.assignment_not_found", "No such assignment.")


async def own(session: AsyncSession, user_id: uuid.UUID, assignment_id: uuid.UUID) -> Assignment:
    """The caller's kitchen's assignment, row-locked; 404 for anyone else's."""
    assignment = await session.get(Assignment, assignment_id, with_for_update=True)
    partner = None if assignment is None else await session.get(Partner, assignment.partner_id)
    if assignment is None or partner is None or partner.user_id != user_id:
        raise not_found()
    return assignment


def _ensure_open(assignment: Assignment, at: datetime) -> None:
    if assignment.status != "offered" or at >= assignment.respond_by:
        raise ProblemError(409, "partners.assignment_closed", "This offer is closed.")


async def event(
    session: AsyncSession,
    assignment: Assignment,
    action: str,
    user_id: uuid.UUID | None,
    client_at: datetime | None = None,
    **payload: object,
) -> None:
    """Record what happened (an offline tap keeps its `client_at`) and bump the version."""
    session.add(
        AssignmentEvent(
            assignment_id=assignment.id,
            action=action,
            user_id=user_id,
            payload=payload,
            client_at=client_at,
        )
    )
    assignment.version += 1
    await session.flush()


async def accept(
    session: AsyncSession, assignment: Assignment, user_id: uuid.UUID, at: datetime
) -> None:
    """Accept the whole offer. M4's `set_kitchen` (ADR-0026 §3) is not built yet, so the
    order does not show its cook until it is."""
    _ensure_open(assignment, at)
    assignment.status, assignment.responded_at = "accepted", at
    await event(session, assignment, "accepted", user_id)


async def decline(
    session: AsyncSession,
    assignment: Assignment,
    reason: str,
    note: str | None,
    user_id: uuid.UUID,
    at: datetime,
) -> None:
    _ensure_open(assignment, at)
    assignment.status, assignment.responded_at, assignment.decline_reason = "declined", at, reason
    await event(session, assignment, "declined", user_id, reason=reason, note=note)
    await release(session, assignment, "declined")


async def release(session: AsyncSession, assignment: Assignment, reason: str) -> list[uuid.UUID]:
    """Let go of the assignment's orders that are not ready yet, for the next round."""
    released = await session.execute(
        update(AssignmentOrder)
        .where(AssignmentOrder.assignment_id == assignment.id, AssignmentOrder.status == "assigned")
        .values(status="released")
        .returning(AssignmentOrder.order_id)
    )
    order_ids = sorted(released.scalars(), key=str)
    await publish(
        session,
        events.ASSIGNMENT_RELEASED,
        {
            "assignment_id": str(assignment.id),
            "partner_id": str(assignment.partner_id),
            "hub_id": str(assignment.hub_id),
            "date": assignment.on_date.isoformat(),
            "meal": assignment.meal,
            "order_ids": [str(order_id) for order_id in order_ids],
            "reason": reason,
        },
    )
    return order_ids


async def expire_overdue(session: AsyncSession, at: datetime) -> int:
    """Offers past `respond_by` become `expired` and release their orders."""
    overdue = (
        (
            await session.execute(
                select(Assignment)
                .where(Assignment.status == "offered", Assignment.respond_by <= at)
                .order_by(Assignment.id)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    for assignment in overdue:
        assignment.status = "expired"
        await event(session, assignment, "expired", None)
        await release(session, assignment, "expired")
    return len(overdue)
