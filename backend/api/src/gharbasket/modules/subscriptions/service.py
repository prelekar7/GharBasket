"""Business logic of M3 Plans & Subscriptions (spec §3.1): plans, the subscription, delivery
days, generating a week's cycle, reading, editing, approving and skipping it, the pantry
feedback after delivery, and the Budget Lock.

Approval hands the box to M4 through `orders.api.create_from_cycle` in the same transaction,
so the answer carries `order_id` and a full slot is refused on the spot; the
`subscriptions.box_approved` event still goes out for M11 and analytics.
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx2
import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.intel_gateway import api as intel_api
from gharbasket.modules.intel_gateway.api import PlanRequest
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.subscriptions import events, planning
from gharbasket.modules.subscriptions.models import (
    BoxLine,
    BudgetLock,
    EditFeedback,
    PantryFeedback,
    Plan,
    SocietyDeliveryDay,
    Subscription,
    WeeklyCycle,
)
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.outbox import publish
from gharbasket.platform.settings import Settings

log = structlog.get_logger("gharbasket.subscriptions")


@asynccontextmanager
async def http_client(given: httpx2.AsyncClient | None) -> AsyncIterator[httpx2.AsyncClient]:
    """The app's shared client, or a short-lived one (tests, tools) that is closed afterwards."""
    if given is not None:
        yield given
        return
    async with httpx2.AsyncClient(timeout=httpx2.Timeout(5.0)) as client:
        yield client


MAX_DELIVERY_DAYS = 3
MAX_QTY = 20  # steps a line may hold
FEEDBACK_DAYS = 14  # the pantry taps stay open this long after delivery
MIN_BUDGET_PAISE = 50_000  # ₹500
EDITABLE = ("awaiting_approval", "approved")
IST = ZoneInfo("Asia/Kolkata")


def IST_DATE() -> date:
    return datetime.now(IST).date()


def now() -> datetime:
    return datetime.now(UTC)


def not_found(
    what: str = "subscriptions.not_found", detail: str = "Nothing here yet."
) -> ProblemError:
    return ProblemError(404, what, detail)


# --- plans and subscriptions --------------------------------------------------------------------


async def list_plans(session: AsyncSession) -> list[Plan]:
    return list(
        (await session.execute(select(Plan).where(Plan.active).order_by(Plan.code))).scalars()
    )


async def subscription_of(session: AsyncSession, household_id: uuid.UUID) -> Subscription | None:
    return (
        await session.execute(select(Subscription).where(Subscription.household_id == household_id))
    ).scalar_one_or_none()


async def subscribe(
    session: AsyncSession, household_id: uuid.UUID, *, plan_code: str, payment_mode: str
) -> Subscription:
    if payment_mode != "wallet":
        raise ProblemError(
            422, "subscriptions.payment_mode_unavailable", "UPI Autopay arrives with the pilot."
        )
    plan = (
        await session.execute(select(Plan).where(Plan.code == plan_code, Plan.active))
    ).scalar_one_or_none()
    if plan is None:
        raise ProblemError(422, "subscriptions.plan_unknown", "No such plan.")
    subscription = Subscription(
        household_id=household_id, plan_id=plan.id, payment_mode=payment_mode
    )
    try:
        async with session.begin_nested():
            session.add(subscription)
            await session.flush()
    except IntegrityError:
        raise ProblemError(
            409, "subscriptions.exists", "This household already has a subscription."
        ) from None
    await session.refresh(subscription)
    return subscription


async def update_subscription(
    session: AsyncSession,
    subscription: Subscription,
    *,
    set_pause: bool = False,
    paused_until: date | None = None,
    auto_approve: bool | None = None,
) -> Subscription:
    """`set_pause` says whether `paused_until` was sent (None then means resume)."""
    if set_pause:
        subscription.paused_until = paused_until
        paused = paused_until is not None and paused_until > IST_DATE()
        subscription.status = "paused" if paused else "active"
        if paused:
            await publish(
                session,
                events.SUBSCRIPTION_PAUSED,
                {
                    "subscription_id": str(subscription.id),
                    "household_id": str(subscription.household_id),
                    "paused_until": str(paused_until),
                },
            )
    if auto_approve is not None:
        subscription.auto_approve = auto_approve
    await session.flush()
    return subscription


def is_paused_on(subscription: Subscription, on: date) -> bool:
    return subscription.paused_until is not None and on < subscription.paused_until


# --- delivery days ------------------------------------------------------------------------------


async def delivery_days(session: AsyncSession, society_id: uuid.UUID) -> list[SocietyDeliveryDay]:
    rows = await session.execute(
        select(SocietyDeliveryDay)
        .where(SocietyDeliveryDay.society_id == society_id)
        .order_by(SocietyDeliveryDay.weekday)
    )
    return list(rows.scalars())


async def set_delivery_days(
    session: AsyncSession,
    society_id: uuid.UUID,
    hub_id: uuid.UUID,
    items: list[tuple[int, uuid.UUID]],
) -> list[SocietyDeliveryDay]:
    if not 1 <= len(items) <= MAX_DELIVERY_DAYS or len({w for w, _ in items}) != len(items):
        raise ProblemError(
            422,
            "subscriptions.delivery_days_invalid",
            f"1 to {MAX_DELIVERY_DAYS} distinct weekdays.",
        )
    for _, slot_id in items:
        slot = await orders_api.slot_ref(session, slot_id)
        if slot is None or slot.hub_id != hub_id or not slot.active or slot.kind != "box":
            raise ProblemError(
                422,
                "subscriptions.delivery_days_invalid",
                "Each day needs an active box slot of this hub.",
            )
    for existing in await delivery_days(session, society_id):
        await session.delete(existing)
    await session.flush()
    rows = [SocietyDeliveryDay(society_id=society_id, weekday=w, slot_id=s) for w, s in items]
    session.add_all(rows)
    await session.flush()
    return sorted(rows, key=lambda r: r.weekday)


def next_delivery(days: list[SocietyDeliveryDay], after: date) -> SocietyDeliveryDay | None:
    """The first delivery day strictly after `after`, with its slot."""
    best: tuple[int, SocietyDeliveryDay] | None = None
    for day in days:
        ahead = (day.weekday - after.weekday()) % 7 or 7
        if best is None or ahead < best[0]:
            best = (ahead, day)
    return None if best is None else best[1]


# --- cycles -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Generated:
    cycle: WeeklyCycle
    lines: list[BoxLine]
    created: bool


def monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


async def cycle_for(
    session: AsyncSession, household_id: uuid.UUID, delivery_date: date
) -> WeeklyCycle | None:
    return (
        await session.execute(
            select(WeeklyCycle).where(
                WeeklyCycle.household_id == household_id, WeeklyCycle.delivery_date == delivery_date
            )
        )
    ).scalar_one_or_none()


async def draft(
    session: AsyncSession,
    http: httpx2.AsyncClient,
    settings: Settings,
    profile: identity_api.HouseholdPlanProfile,
    catalog: catalog_api.PlanCatalog,
    delivery_date: date,
    *,
    record_for: uuid.UUID | None,
) -> tuple[planning.Draft, str, uuid.UUID | None]:
    """A box for the week of `delivery_date`: (draft, plan source, prediction id)."""
    monday = monday_of(delivery_date)
    festivals = await catalog_api.festivals_between(session, monday, monday + timedelta(days=6))
    request = planning.plan_request(profile, catalog, monday, festivals)
    plan = await intel_api.plan_week(
        http,
        settings,
        PlanRequest.model_validate(request),
        session=session if record_for else None,
        household_id=record_for,
        week_monday=monday,
    )
    meals = [m.model_dump() for m in plan.plan] if plan.plan else None
    lines = planning.lines_from_plan(profile, catalog, meals, fallback=plan.plan is None)
    monthly_budget = profile.budget_paise
    budget_near = (
        monthly_budget is not None
        and sum(line.total_paise for line in lines) * 30 // 7 > monthly_budget
    )
    draft_box = planning.Draft(
        lines=lines,
        meal_plan=planning.meal_plan_days(meals, catalog, monday),
        highlights=planning.highlights_for(plan.source, request["fasting_days"], budget_near),
    )
    prediction = None
    if record_for:
        kept = await intel_api.latest_prediction(
            session, kind="weekly_plan", subject_id=record_for, for_date=monday
        )
        prediction = kept.id if kept else None
    return draft_box, plan.source, prediction


async def generate_cycle(
    session: AsyncSession,
    http: httpx2.AsyncClient,
    settings: Settings,
    *,
    household_id: uuid.UUID,
    delivery_date: date,
    catalog: catalog_api.PlanCatalog,
    notice_now: bool = True,
) -> Generated | None:
    """The household's cycle for `delivery_date`, created once; None when nothing applies
    (no subscription, paused, no delivery day for the society, no household)."""
    existing = await cycle_for(session, household_id, delivery_date)
    if existing is not None:
        lines = list(
            (
                await session.execute(select(BoxLine).where(BoxLine.cycle_id == existing.id))
            ).scalars()
        )
        return Generated(existing, lines, False)
    profile = await identity_api.household_plan_profile(session, household_id)
    if profile is None or profile.hub_id is None or not profile.autopilot_enabled:
        return None
    subscription = await subscription_of(session, household_id)
    if (
        subscription is None
        or subscription.status == "ended"
        or is_paused_on(subscription, delivery_date)
    ):
        return None
    day = next(
        (
            d
            for d in await delivery_days(session, profile.society_id)
            if d.weekday == delivery_date.weekday()
        ),
        None,
    )
    if day is None:
        return None
    box, source, prediction = await draft(
        session, http, settings, profile, catalog, delivery_date, record_for=household_id
    )
    cycle = WeeklyCycle(
        household_id=household_id,
        hub_id=profile.hub_id,
        society_id=profile.society_id,
        subscription_id=subscription.id,
        week_start=monday_of(delivery_date),
        delivery_date=delivery_date,
        slot_id=day.slot_id,
        cutoff_at=orders_api.cutoff_for(delivery_date),
        plan_prediction_id=prediction,
        plan_source=source,
        meal_plan=box.meal_plan,
        highlights=box.highlights,
        status="awaiting_approval" if notice_now else "draft",
        generated_at=now(),
        notice_sent_at=now() if notice_now else None,
    )
    try:
        async with session.begin_nested():
            session.add(cycle)
            await session.flush()
    except IntegrityError:  # generated twice at the same moment
        found = await cycle_for(session, household_id, delivery_date)
        if found is None:
            raise
        lines = list(
            (await session.execute(select(BoxLine).where(BoxLine.cycle_id == found.id))).scalars()
        )
        return Generated(found, lines, False)
    lines = [
        BoxLine(
            cycle_id=cycle.id,
            product_id=line.product_id,
            qty=line.qty,
            step_qty=line.step_qty,
            step_unit=line.step_unit,
            unit_price_paise=line.unit_price_paise,
            reason_code=line.reason_code,
            reason_params=line.reason_params,
            source=line.source,
        )
        for line in box.lines
    ]
    session.add_all(lines)
    await session.flush()
    await publish(
        session,
        events.CYCLE_GENERATED,
        {
            "cycle_id": str(cycle.id),
            "household_id": str(household_id),
            "hub_id": str(profile.hub_id),
            "delivery_date": delivery_date.isoformat(),
            "total_paise": box.total_paise,
            "source": source,
        },
    )
    await session.refresh(cycle)
    return Generated(cycle, lines, True)


async def generate_for_hub(
    session: AsyncSession,
    http: httpx2.AsyncClient,
    settings: Settings,
    *,
    hub_id: uuid.UUID,
    delivery_date: date,
) -> dict[str, int]:
    """The job's body for one hub: a cycle per Autopilot household due that day."""
    catalog = await catalog_api.plan_catalog(session, hub_id)
    counts = {"created": 0, "existing": 0, "skipped": 0}
    for household_id in await identity_api.households_of_hub(session, hub_id):
        result = await generate_cycle(
            session,
            http,
            settings,
            household_id=household_id,
            delivery_date=delivery_date,
            catalog=catalog,
        )
        if result is None:
            counts["skipped"] += 1
        elif result.created:
            counts["created"] += 1
        else:
            counts["existing"] += 1
    return counts


async def current_cycle(
    session: AsyncSession, household_id: uuid.UUID, today: date
) -> WeeklyCycle | None:
    """The cycle whose delivery is next (today or later), else the latest one."""
    upcoming = (
        await session.execute(
            select(WeeklyCycle)
            .where(WeeklyCycle.household_id == household_id, WeeklyCycle.delivery_date >= today)
            .order_by(WeeklyCycle.delivery_date)
            .limit(1)
        )
    ).scalar_one_or_none()
    if upcoming is not None:
        return upcoming
    return (
        await session.execute(
            select(WeeklyCycle)
            .where(WeeklyCycle.household_id == household_id)
            .order_by(WeeklyCycle.delivery_date.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def get_cycle(
    session: AsyncSession, household_id: uuid.UUID, cycle_id: uuid.UUID
) -> WeeklyCycle:
    cycle = await session.get(WeeklyCycle, cycle_id)
    if cycle is None or cycle.household_id != household_id:
        raise not_found("subscriptions.no_cycle", "No such week.")
    return cycle


async def lines_of(session: AsyncSession, cycle_id: uuid.UUID) -> list[BoxLine]:
    rows = await session.execute(
        select(BoxLine).where(BoxLine.cycle_id == cycle_id).order_by(BoxLine.product_id)
    )
    return list(rows.scalars())


async def budget_lock_for(
    session: AsyncSession, household_id: uuid.UUID, month: date
) -> BudgetLock | None:
    return (
        await session.execute(
            select(BudgetLock).where(
                BudgetLock.household_id == household_id, BudgetLock.month == month.replace(day=1)
            )
        )
    ).scalar_one_or_none()


async def spent_in_month(
    session: AsyncSession,
    household_id: uuid.UUID,
    month: date,
    *,
    except_cycle: uuid.UUID | None = None,
) -> int:
    """What the month's approved and locked boxes cost (skipped weeks spend nothing)."""
    first = month.replace(day=1)
    following = (first + timedelta(days=32)).replace(day=1)
    cycles = [
        cycle_id
        for cycle_id in (
            await session.execute(
                select(WeeklyCycle.id).where(
                    WeeklyCycle.household_id == household_id,
                    WeeklyCycle.delivery_date >= first,
                    WeeklyCycle.delivery_date < following,
                    WeeklyCycle.status.in_(("approved", "locked")),
                )
            )
        ).scalars()
        if cycle_id != except_cycle
    ]
    if not cycles:
        return 0
    lines = (await session.execute(select(BoxLine).where(BoxLine.cycle_id.in_(cycles)))).scalars()
    return sum(line.qty * line.unit_price_paise for line in lines)


async def set_budget_lock(
    session: AsyncSession, household_id: uuid.UUID, month: date, budget_paise: int | None
) -> BudgetLock | None:
    """Set the month's Budget Lock (this month or next, ≥ ₹500), or switch it off with None."""
    first = month.replace(day=1)
    this_month = IST_DATE().replace(day=1)
    next_month = (this_month + timedelta(days=32)).replace(day=1)
    if first not in (this_month, next_month):
        raise ProblemError(
            422, "subscriptions.budget_invalid", "A Budget Lock is for this month or the next."
        )
    if budget_paise is not None and budget_paise < MIN_BUDGET_PAISE:
        raise ProblemError(422, "subscriptions.budget_invalid", "At least ₹500 a month.")
    lock = await budget_lock_for(session, household_id, first)
    if budget_paise is None:
        if lock is not None:
            await session.delete(lock)
            await session.flush()
        return None
    if lock is None:
        lock = BudgetLock(household_id=household_id, month=first, budget_paise=budget_paise)
        session.add(lock)
    else:
        lock.budget_paise = budget_paise
    await session.flush()
    return lock


# --- edits, approval, skip ----------------------------------------------------------------------


@dataclass(frozen=True)
class Op:
    op: str  # remove | add | qty | substitute
    product_id: uuid.UUID
    qty: int | None = None
    substitute_id: uuid.UUID | None = None


def not_editable(cycle: WeeklyCycle) -> ProblemError:
    return ProblemError(
        409, "subscriptions.not_editable", f"A {cycle.status.replace('_', ' ')} week cannot change."
    )


def check_editable(cycle: WeeklyCycle, *, expected_version: int | None = None) -> None:
    if expected_version is not None and cycle.version != expected_version:
        raise ProblemError(412, "subscriptions.version_conflict", "The week changed; reload it.")
    if cycle.status not in EDITABLE:
        raise not_editable(cycle)
    if now() >= cycle.cutoff_at:
        raise ProblemError(
            409, "subscriptions.cutoff_passed", "The cut-off for this week has passed."
        )


def _qty(value: int | None) -> int:
    if value is None or not 1 <= value <= MAX_QTY:
        raise ProblemError(422, "subscriptions.qty_invalid", f"Quantity is 1 to {MAX_QTY} steps.")
    return value


async def _priced(
    session: AsyncSession, product_id: uuid.UUID, hub_id: uuid.UUID, code: str
) -> catalog_api.LineProduct:
    product = (await catalog_api.line_products(session, [product_id], hub_id=hub_id)).get(
        product_id
    )
    if product is None or product.price_paise is None:
        raise ProblemError(422, code, "Not a product your hub sells this week.")
    return product


async def edit_lines(
    session: AsyncSession, cycle: WeeklyCycle, ops: list[Op], *, expected_version: int
) -> list[BoxLine]:
    """Apply the customer's ops in order; every op leaves an edit_feedback row (the ranker's
    labels). With a Budget Lock on, the box must fit the month's remaining budget."""
    check_editable(cycle, expected_version=expected_version)
    lines = {line.product_id: line for line in await lines_of(session, cycle.id)}
    for op in ops:
        line = lines.get(op.product_id)
        if op.op == "add":
            product = await _priced(
                session, op.product_id, cycle.hub_id, "subscriptions.product_unknown"
            )
            qty = _qty(op.qty)
            if line is None:
                line = BoxLine(
                    cycle_id=cycle.id,
                    product_id=product.id,
                    qty=qty,
                    step_qty=product.step_qty,
                    step_unit=product.step_unit,
                    unit_price_paise=product.price_paise or 0,
                    reason_code="added_by_you",
                    reason_params=None,
                    source="customer",
                )
                session.add(line)
                lines[product.id] = line
            else:
                line.qty = qty
            action = "added"
        elif line is None:
            raise ProblemError(422, "subscriptions.product_unknown", "That item is not in the box.")
        elif op.op == "remove":
            await session.delete(line)
            del lines[op.product_id]
            action = "removed"
        elif op.op == "qty":
            qty = _qty(op.qty)
            if qty == line.qty:
                continue
            action = "qty_up" if qty > line.qty else "qty_down"
            line.qty = qty
        else:  # substitute
            if op.substitute_id is None or op.substitute_id not in await catalog_api.substitute_ids(
                session, op.product_id, cycle.hub_id
            ):
                raise ProblemError(
                    422, "subscriptions.substitute_unknown", "Pick a substitute from the list."
                )
            product = await _priced(
                session, op.substitute_id, cycle.hub_id, "subscriptions.substitute_unknown"
            )
            original = (
                await catalog_api.line_products(session, [op.product_id], hub_id=cycle.hub_id)
            ).get(op.product_id)
            qty = line.qty
            await session.delete(line)
            del lines[op.product_id]
            await session.flush()
            swapped = lines.get(product.id)
            if swapped is None:
                swapped = BoxLine(
                    cycle_id=cycle.id,
                    product_id=product.id,
                    qty=qty,
                    step_qty=product.step_qty,
                    step_unit=product.step_unit,
                    unit_price_paise=product.price_paise or 0,
                    reason_code="swapped_by_you",
                    reason_params={"from": original.slug if original else str(op.product_id)},
                    source="customer",
                    replaces_product_id=op.product_id,
                )
                session.add(swapped)
                lines[product.id] = swapped
            else:
                swapped.qty = min(MAX_QTY, swapped.qty + qty)
            action = "swapped"
        session.add(
            EditFeedback(
                cycle_id=cycle.id,
                product_id=op.product_id,
                action=action,
                substitute_product_id=op.substitute_id if action == "swapped" else None,
                at=now(),
            )
        )
        await session.flush()
    total = sum(line.qty * line.unit_price_paise for line in lines.values())
    lock = await budget_lock_for(session, cycle.household_id, cycle.delivery_date)
    if lock is not None:
        spent = await spent_in_month(
            session, cycle.household_id, cycle.delivery_date, except_cycle=cycle.id
        )
        if spent + total > lock.budget_paise:
            raise ProblemError(
                422,
                "subscriptions.budget_exceeded",
                "This box goes over your Budget Lock for the month.",
                extra={"over_paise": spent + total - lock.budget_paise},
            )
    cycle.version += 1
    await session.flush()
    result = sorted(lines.values(), key=lambda line: line.product_id)
    if cycle.status == "approved":  # the order already exists: hand the new lines over
        await _hand_over(session, cycle, result)
    return result


def approved_cycle(cycle: WeeklyCycle, lines: list[BoxLine]) -> orders_api.ApprovedCycle:
    return orders_api.ApprovedCycle(
        cycle_id=cycle.id,
        household_id=cycle.household_id,
        hub_id=cycle.hub_id,
        society_id=cycle.society_id,
        delivery_date=cycle.delivery_date,
        slot_id=cycle.slot_id,
        lines=tuple(
            orders_api.CycleLine(
                line.product_id,
                line.qty,
                line.unit_price_paise,
                line.reason_code,
                line.reason_params,
            )
            for line in lines
        ),
        data_source=cycle.data_source,
        version=cycle.version,
    )


async def _hand_over(session: AsyncSession, cycle: WeeklyCycle, lines: list[BoxLine]) -> None:
    """Create (or refresh) the M4 order and publish `box_approved` with the same lines."""
    if not lines:
        raise ProblemError(422, "subscriptions.qty_invalid", "The box is empty; add something.")
    handed = approved_cycle(cycle, lines)
    order = await orders_api.create_from_cycle(session, handed)
    cycle.order_id = order.id
    await publish(
        session,
        events.BOX_APPROVED,
        {
            "cycle_id": str(cycle.id),
            "household_id": str(cycle.household_id),
            "hub_id": str(cycle.hub_id),
            "society_id": str(cycle.society_id),
            "delivery_date": cycle.delivery_date.isoformat(),
            "slot_id": str(cycle.slot_id),
            "version": cycle.version,
            "lines": [
                {
                    "product_id": str(line.product_id),
                    "qty": line.qty,
                    "unit_price_paise": line.unit_price_paise,
                    "reason_code": line.reason_code,
                    "reason_params": line.reason_params,
                }
                for line in handed.lines
            ],
            "total_paise": sum(line.qty * line.unit_price_paise for line in handed.lines),
        },
    )


async def approve(
    session: AsyncSession,
    cycle: WeeklyCycle,
    *,
    expected_version: int | None,
    actor: str = "customer",
) -> WeeklyCycle:
    """Approve before the cut-off: the box becomes an M4 order with these lines and prices.
    Approving an approved week again changes nothing."""
    check_editable(cycle, expected_version=expected_version)
    if cycle.status == "approved":
        return cycle
    cycle.status = "approved"
    cycle.approved_at = now()
    cycle.auto_approved = actor == "system"
    cycle.version += 1
    await session.flush()
    await _hand_over(session, cycle, await lines_of(session, cycle.id))
    await session.flush()
    return cycle


async def skip(session: AsyncSession, cycle: WeeklyCycle) -> WeeklyCycle:
    """Skip the week before the cut-off; an order already created is cancelled (M4)."""
    if cycle.status == "skipped":
        return cycle
    check_editable(cycle)
    await orders_api.cancel_from_cycle(session, cycle.id)
    cycle.status = "skipped"
    cycle.version += 1
    await session.flush()
    await publish(
        session,
        events.BOX_SKIPPED,
        {
            "cycle_id": str(cycle.id),
            "household_id": str(cycle.household_id),
            "delivery_date": cycle.delivery_date.isoformat(),
        },
    )
    return cycle


async def auto_approve_due(session: AsyncSession, delivery_date: date) -> dict[str, int]:
    """The `apply_auto_approve` job's body: approve what the household asked us to, when the
    box-ready notice went out at least 24 h before the cut-off."""
    rows = await session.execute(
        select(WeeklyCycle)
        .join(Subscription, Subscription.id == WeeklyCycle.subscription_id)
        .where(
            WeeklyCycle.delivery_date == delivery_date,
            WeeklyCycle.status == "awaiting_approval",
            Subscription.auto_approve.is_(True),
            WeeklyCycle.notice_sent_at.is_not(None),
            WeeklyCycle.notice_sent_at <= WeeklyCycle.cutoff_at - timedelta(hours=24),
        )
        .order_by(WeeklyCycle.id)
    )
    counts = {"approved": 0, "failed": 0}
    for cycle in rows.scalars().all():
        try:
            async with session.begin_nested():
                await approve(session, cycle, expected_version=None, actor="system")
        except ProblemError as exc:  # a full slot, an empty box: the customer decides by hand
            counts["failed"] += 1
            log.warning("auto_approve_failed", cycle_id=str(cycle.id), code=exc.code)
            continue
        counts["approved"] += 1
    return counts


# --- pantry feedback ----------------------------------------------------------------------------


async def record_feedback(
    session: AsyncSession,
    cycle: WeeklyCycle,
    items: dict[uuid.UUID, str],
    *,
    today: date,
) -> int:
    """Ran out / wasted / just right per item, from delivery day for 14 days; one row per
    product, the latest tap wins."""
    window_open = cycle.status not in (
        "skipped",
        "cancelled",
    ) and cycle.delivery_date <= today <= cycle.delivery_date + timedelta(days=FEEDBACK_DAYS)
    if not window_open:
        raise ProblemError(
            422,
            "subscriptions.feedback_window_closed",
            f"Tell us from delivery day for {FEEDBACK_DAYS} days.",
        )
    on_box = {line.product_id for line in await lines_of(session, cycle.id)}
    unknown = [str(pid) for pid in items if pid not in on_box]
    if unknown:
        raise ProblemError(
            422,
            "subscriptions.product_unknown",
            "That item was not in this box.",
            extra={"product_ids": unknown},
        )
    existing = {
        row.product_id: row
        for row in (
            await session.execute(
                select(PantryFeedback).where(
                    PantryFeedback.cycle_id == cycle.id, PantryFeedback.product_id.in_(items)
                )
            )
        ).scalars()
    }
    for product_id, signal in items.items():
        row = existing.get(product_id)
        if row is None:
            session.add(
                PantryFeedback(
                    cycle_id=cycle.id,
                    household_id=cycle.household_id,
                    product_id=product_id,
                    signal=signal,
                    at=now(),
                )
            )
        else:
            row.signal = signal
            row.at = now()
    await session.flush()
    return len(items)
