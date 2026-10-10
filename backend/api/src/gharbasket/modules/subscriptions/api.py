"""Facade of M3 Plans & Subscriptions: the only module other modules may import."""

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.subscriptions import service
from gharbasket.modules.subscriptions.models import (
    BoxLine,
    BudgetLock,
    EditFeedback,
    PantryFeedback,
    WeeklyCycle,
)
from gharbasket.platform.locale import Language, localised

MAX_QTY = service.MAX_QTY  # steps a line may hold

__all__ = [
    "MAX_QTY",
    "LineEdit",
    "WeekLineView",
    "WeekView",
    "WeeklyCycle",
    "current_cycle_of",
    "cycle_after",
    "cycle_for",
    "edit_cycle_lines",
    "export_for_user",
    "mark_locked",
    "skip_cycle",
    "week_view",
]


@dataclass(frozen=True)
class LineEdit:
    op: str  # remove | add | qty
    product_id: uuid.UUID
    qty: int | None = None


async def cycle_for(
    session: AsyncSession, household_id: uuid.UUID, delivery_date: date
) -> WeeklyCycle | None:
    return await service.cycle_for(session, household_id, delivery_date)


async def current_cycle_of(session: AsyncSession, household_id: uuid.UUID) -> WeeklyCycle | None:
    """The household's week as GET /v1/cycles/current shows it (M13 confirms against it)."""
    return await service.current_cycle(session, household_id, service.IST_DATE())


def _summary(cycle: WeeklyCycle, lines: list[Any]) -> dict[str, Any]:
    return {
        "cycle_id": str(cycle.id),
        "status": cycle.status,
        "version": cycle.version,
        "total_paise": sum(line.qty * line.unit_price_paise for line in lines),
    }


async def cycle_after(
    session: AsyncSession, household_id: uuid.UUID, delivery_date: date
) -> WeeklyCycle | None:
    """The household's next week after this delivery date, if it has been generated."""
    return (
        await session.execute(
            select(WeeklyCycle)
            .where(
                WeeklyCycle.household_id == household_id,
                WeeklyCycle.delivery_date > delivery_date,
            )
            .order_by(WeeklyCycle.delivery_date)
            .limit(1)
        )
    ).scalar_one_or_none()


@dataclass(frozen=True)
class WeekLineView:
    product_id: uuid.UUID
    slug: str
    name: str
    qty: int
    unit: str  # one step, e.g. "250 g"
    unit_price_paise: int
    line_total_paise: int
    reason_code: str


@dataclass(frozen=True)
class WeekView:
    """A week as the assistant may show it (M13 §3): product facts and prices, no household."""

    cycle_id: uuid.UUID
    version: int
    delivery_date: date
    status: str
    cutoff_at: datetime
    can_edit: bool
    total_paise: int
    lines: tuple[WeekLineView, ...]
    meals: tuple[tuple[str, str, tuple[str, ...]], ...]  # (date, slot, dish names)


async def week_view(
    session: AsyncSession, cycle: WeeklyCycle, hub_id: uuid.UUID | None, lang: Language
) -> WeekView:
    lines = await service.lines_of(session, cycle.id)
    products = await catalog_api.line_products(
        session, [line.product_id for line in lines], hub_id=hub_id
    )
    recipes = {str(r.id): r for r in (await catalog_api.plan_catalog(session, None)).recipes}
    views = []
    for line in lines:
        product = products.get(line.product_id)
        views.append(
            WeekLineView(
                product_id=line.product_id,
                slug=product.slug if product else "",
                name=localised(product.names, lang) if product else "",
                qty=line.qty,
                unit=catalog_api.step_label(line.step_qty, line.step_unit, lang),
                unit_price_paise=line.unit_price_paise,
                line_total_paise=line.qty * line.unit_price_paise,
                reason_code=line.reason_code,
            )
        )
    meals = []
    for day in cycle.meal_plan or []:
        for meal in day.get("meals", []):
            recipe = recipes.get(str(meal.get("recipe_id")))
            dishes = ["tiffin"] if meal.get("tiffin") else []
            if recipe is not None:
                dishes.append(localised(recipe.names, lang))
            dishes += [str(side).replace("-", " ") for side in meal.get("sides", [])]
            meals.append((str(day["date"]), str(meal["slot"]), tuple(dishes)))
    return WeekView(
        cycle_id=cycle.id,
        version=cycle.version,
        delivery_date=cycle.delivery_date,
        status=cycle.status,
        cutoff_at=cycle.cutoff_at,
        can_edit=cycle.status in service.EDITABLE and service.now() < cycle.cutoff_at,
        total_paise=sum(view.line_total_paise for view in views),
        lines=tuple(views),
        meals=tuple(meals),
    )


async def _locked(
    session: AsyncSession, household_id: uuid.UUID, cycle_id: uuid.UUID
) -> WeeklyCycle:
    """The household's cycle, its row held (FOR UPDATE) so two changes made at once are
    applied one after the other and the version check sees the first."""
    await session.execute(
        select(WeeklyCycle)
        .where(WeeklyCycle.id == cycle_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return await service.get_cycle(session, household_id, cycle_id)


async def edit_cycle_lines(
    session: AsyncSession,
    household_id: uuid.UUID,
    cycle_id: uuid.UUID,
    edits: list[LineEdit],
    *,
    expected_version: int,
) -> dict[str, Any]:
    """Apply edits to the household's own week exactly as PATCH /v1/cycles/{id}/lines would,
    against the version the person was shown (412 `subscriptions.version_conflict` otherwise)."""
    cycle = await _locked(session, household_id, cycle_id)
    ops = [service.Op(edit.op, edit.product_id, qty=edit.qty) for edit in edits]
    lines = await service.edit_lines(session, cycle, ops, expected_version=expected_version)
    return _summary(cycle, lines)


async def skip_cycle(
    session: AsyncSession, household_id: uuid.UUID, cycle_id: uuid.UUID, *, expected_version: int
) -> dict[str, Any]:
    """Skip the household's own week as POST /v1/cycles/{id}/skip would, against the version
    the person was shown (412 `subscriptions.version_conflict` otherwise)."""
    cycle = await _locked(session, household_id, cycle_id)
    if cycle.status != "skipped":
        service.check_editable(cycle, expected_version=expected_version)
    await service.skip(session, cycle)
    return _summary(cycle, await service.lines_of(session, cycle.id))


async def mark_locked(session: AsyncSession, cycle_id: uuid.UUID, order_id: uuid.UUID) -> None:
    """M4 locked the order at the cut-off: mirror it on the cycle (the outbox consumer in
    jobs.py does this; the facade stays for tools and tests)."""
    cycle = await session.get(WeeklyCycle, cycle_id)
    if cycle is not None:
        await service.mark_locked(session, cycle, order_id)


async def export_for_user(session: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
    """M3's section of the user's data export (M1 §3.3), read-only: the subscription, every cycle
    with its meal plan and lines, Budget Locks, and edit and pantry feedback."""
    household = await identity_api.household_for_user(session, user_id)
    if household is None:
        return {"subscription": None, "cycles": [], "budget_locks": [], "pantry_feedback": []}
    subscription = await service.subscription_of(session, household.id)
    cycles = list(
        (
            await session.execute(
                select(WeeklyCycle)
                .where(WeeklyCycle.household_id == household.id)
                .order_by(WeeklyCycle.delivery_date)
            )
        ).scalars()
    )
    ids = [cycle.id for cycle in cycles]
    lines = (await session.execute(select(BoxLine).where(BoxLine.cycle_id.in_(ids)))).scalars()
    edits = (
        await session.execute(
            select(EditFeedback).where(EditFeedback.cycle_id.in_(ids)).order_by(EditFeedback.id)
        )
    ).scalars()
    by_cycle: dict[uuid.UUID, dict[str, list[dict[str, Any]]]] = {
        i: {"lines": [], "edits": []} for i in ids
    }
    for line in lines:
        by_cycle[line.cycle_id]["lines"].append(line.as_dict(exclude={"cycle_id"}))
    for edit in edits:
        by_cycle[edit.cycle_id]["edits"].append(edit.as_dict(exclude={"cycle_id"}))
    locks = await session.execute(
        select(BudgetLock).where(BudgetLock.household_id == household.id).order_by(BudgetLock.month)
    )
    pantry = await session.execute(
        select(PantryFeedback)
        .where(PantryFeedback.household_id == household.id)
        .order_by(PantryFeedback.id)
    )
    return {
        "subscription": None if subscription is None else subscription.as_dict(),
        # The plan's ML prediction id is derived ML data: left out (M1 §3.3).
        "cycles": [
            {**c.as_dict(exclude={"plan_prediction_id", "data_source"}), **by_cycle[c.id]}
            for c in cycles
        ],
        "budget_locks": [lock.as_dict() for lock in locks.scalars()],
        "pantry_feedback": [signal.as_dict() for signal in pantry.scalars()],
    }
