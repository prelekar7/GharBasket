"""Business logic of M3 Plans & Subscriptions (spec §3.1): plans, the subscription, delivery
days, generating a week's cycle, and reading cycles. Edits, approval, skip and feedback
follow in part 2.
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
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
IST = ZoneInfo("Asia/Kolkata")


def IST_DATE() -> date:
    return datetime.now(IST).date()


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
        notice_sent_at=datetime.now(UTC) if notice_now else None,
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


async def spent_in_month(session: AsyncSession, household_id: uuid.UUID, month: date) -> int:
    """What the month's approved and locked boxes cost (skipped weeks spend nothing)."""
    first = month.replace(day=1)
    following = (first + timedelta(days=32)).replace(day=1)
    cycles = list(
        (
            await session.execute(
                select(WeeklyCycle.id).where(
                    WeeklyCycle.household_id == household_id,
                    WeeklyCycle.delivery_date >= first,
                    WeeklyCycle.delivery_date < following,
                    WeeklyCycle.status.in_(("approved", "locked")),
                )
            )
        ).scalars()
    )
    if not cycles:
        return 0
    lines = (await session.execute(select(BoxLine).where(BoxLine.cycle_id.in_(cycles)))).scalars()
    return sum(line.qty * line.unit_price_paise for line in lines)


def as_any(value: Any) -> Any:
    return value
