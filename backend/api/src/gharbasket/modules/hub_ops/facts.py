"""Fact rows of M6 for M12's analytics refresh (M12 §3.1): one row per hub and delivery date that
has packing tasks, with grams and paise only.

What M6 cannot say yet is returned as 0 or None, never guessed:
- **Waste** (`waste_*_g`) is 0: waste logs and their cascade land with M6 §3.5's waste PR.
- **Surplus** offered to and taken by NGOs is 0: surplus offers come with the same PR and M9 §3.5.
- **COGS** (`cogs_paise`) is None: paid lines leave stock without lot moves (the 12:30 draw-down
  is not built), so no lot cost can be tied to what was packed.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.hub_ops.models import Hub, PackingLine, PackingTask


@dataclass(frozen=True)
class HubDayFact:
    hub_id: uuid.UUID
    on: date  # the delivery date
    # Picked steps of packed tasks in grams (ml as grams); lines of unknown weight are left out.
    packed_g: int
    waste_ngo_g: int
    waste_bin_g: int
    waste_kitchen_g: int
    waste_imperfect_g: int
    cogs_paise: int | None
    surplus_offered_g: int
    surplus_taken_g: int
    data_source: str  # the hub's


async def hub_day_facts(
    session: AsyncSession,
    *,
    date_from: date,
    date_to: date,
    hub_ids: list[uuid.UUID] | None = None,
) -> list[HubDayFact]:
    """Hub-days between the two IST delivery dates (inclusive) that have packing tasks."""
    days = (
        select(PackingTask.hub_id, PackingTask.delivery_date, Hub.data_source)
        .join(Hub, Hub.id == PackingTask.hub_id)
        .where(PackingTask.delivery_date >= date_from, PackingTask.delivery_date <= date_to)
        .group_by(PackingTask.hub_id, PackingTask.delivery_date, Hub.data_source)
    )
    if hub_ids is not None:
        days = days.where(PackingTask.hub_id.in_(hub_ids))
    found = (await session.execute(days)).all()
    if not found:
        return []
    lines = select(
        PackingTask.hub_id,
        PackingTask.delivery_date,
        func.coalesce(PackingLine.substitute_product_id, PackingLine.product_id),
        PackingLine.picked_qty,
        PackingLine.step_qty,
        PackingLine.step_unit,
    ).join(PackingTask, PackingTask.id == PackingLine.task_id)
    lines = lines.where(
        PackingTask.status == "packed",
        PackingTask.delivery_date >= date_from,
        PackingTask.delivery_date <= date_to,
        PackingLine.picked_qty > 0,
    )
    if hub_ids is not None:
        lines = lines.where(PackingTask.hub_id.in_(hub_ids))
    picked = (await session.execute(lines)).all()
    weights = await catalog_api.line_grams(
        session, [(product, steps, qty, unit) for _, _, product, steps, qty, unit in picked]
    )
    grams: dict[tuple[uuid.UUID, date], float] = defaultdict(float)
    for (hub_id, on, *_), weight in zip(picked, weights, strict=True):
        grams[(hub_id, on)] += weight or 0
    return [
        HubDayFact(
            hub_id=hub_id,
            on=on,
            packed_g=round(grams[(hub_id, on)]),
            waste_ngo_g=0,
            waste_bin_g=0,
            waste_kitchen_g=0,
            waste_imperfect_g=0,
            cogs_paise=None,
            surplus_offered_g=0,
            surplus_taken_g=0,
            data_source=data_source,
        )
        for hub_id, on, data_source in sorted(found, key=lambda row: (row[1], str(row[0])))
    ]
