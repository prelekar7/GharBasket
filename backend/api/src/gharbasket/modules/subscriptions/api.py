"""Facade of M3 Plans & Subscriptions: the only module other modules may import."""

import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.subscriptions import service
from gharbasket.modules.subscriptions.models import WeeklyCycle

__all__ = [
    "LineEdit",
    "WeeklyCycle",
    "current_cycle_of",
    "cycle_for",
    "edit_cycle_lines",
    "mark_locked",
    "skip_cycle",
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


async def edit_cycle_lines(
    session: AsyncSession, household_id: uuid.UUID, cycle_id: uuid.UUID, edits: list[LineEdit]
) -> dict[str, Any]:
    """Apply edits to the household's own week exactly as PATCH /v1/cycles/{id}/lines would,
    against the week's current version (the caller has just shown it to the person)."""
    cycle = await service.get_cycle(session, household_id, cycle_id)
    ops = [service.Op(edit.op, edit.product_id, qty=edit.qty) for edit in edits]
    lines = await service.edit_lines(session, cycle, ops, expected_version=cycle.version)
    return _summary(cycle, lines)


async def skip_cycle(
    session: AsyncSession, household_id: uuid.UUID, cycle_id: uuid.UUID
) -> dict[str, Any]:
    cycle = await service.get_cycle(session, household_id, cycle_id)
    await service.skip(session, cycle)
    return _summary(cycle, await service.lines_of(session, cycle.id))


async def mark_locked(session: AsyncSession, cycle_id: uuid.UUID, order_id: uuid.UUID) -> None:
    """M4 locked the order at the cut-off: mirror it on the cycle (the outbox consumer in
    jobs.py does this; the facade stays for tools and tests)."""
    cycle = await session.get(WeeklyCycle, cycle_id)
    if cycle is not None:
        await service.mark_locked(session, cycle, order_id)
