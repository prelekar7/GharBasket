"""Facade of M3 Plans & Subscriptions: the only module other modules may import."""

import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.subscriptions import service
from gharbasket.modules.subscriptions.models import (
    BoxLine,
    BudgetLock,
    EditFeedback,
    PantryFeedback,
    WeeklyCycle,
)

__all__ = ["WeeklyCycle", "cycle_for", "delivery_dates", "export_for_user", "mark_locked"]


async def cycle_for(
    session: AsyncSession, household_id: uuid.UUID, delivery_date
) -> WeeklyCycle | None:
    return await service.cycle_for(session, household_id, delivery_date)


async def mark_locked(session: AsyncSession, cycle_id: uuid.UUID, order_id: uuid.UUID) -> None:
    """M4 locked the order at the cut-off: mirror it on the cycle (the outbox consumer in
    jobs.py does this; the facade stays for tools and tests)."""
    cycle = await session.get(WeeklyCycle, cycle_id)
    if cycle is not None:
        await service.mark_locked(session, cycle, order_id)


async def delivery_dates(
    session: AsyncSession, society_id: uuid.UUID, after: date, n: int
) -> list[date]:
    """The society's next `n` delivery dates strictly after `after`, soonest first; [] without
    delivery days (M9's donation pickup dates, M9 §3.4)."""
    weekdays = {day.weekday for day in await service.delivery_days(session, society_id)}
    found: list[date] = []
    day = after
    while weekdays and len(found) < n:
        day += timedelta(days=1)
        if day.weekday() in weekdays:
            found.append(day)
    return found


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
