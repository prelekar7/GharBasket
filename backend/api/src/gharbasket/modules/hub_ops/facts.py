"""Fact rows of M6 for M12's analytics refresh (M12 §3.1): one row per hub and day that has packing
tasks (by delivery date) or waste logs (by the IST day they were logged), with grams and paise only.

- **Waste** (`waste_*_g`) is the `kg` of the hub's waste logs of that IST day, by `cascade_to`, in
  grams; a log without a weight is left out, as in M6 §3.5's summary. Only `bin` is waste for the
  KPI (M6 §3.5); `kitchen` and `ngo` are food rescued and kept apart.

What M6 cannot say yet is returned as 0 or None, never guessed:
- **Imperfect Sabzi** (`waste_imperfect_g`) is 0: it is not in v1.
- **Surplus** offered to and taken by NGOs is 0 until the surplus cascade (M6 §3.5, M9 §3.5) lands.
- **COGS** (`cogs_paise`) is None: paid lines leave stock without lot moves (the 12:30 draw-down
  is not built), so no lot cost can be tied to what was packed.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import Date, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.hub_ops.models import Hub, PackingLine, PackingTask, WasteLog

IST = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class HubDayFact:
    hub_id: uuid.UUID
    on: date  # the delivery date of the packing tasks, and the IST day of the waste logs
    # Picked steps of packed tasks in grams (ml as grams); lines of unknown weight are left out.
    packed_g: int
    waste_ngo_g: int  # rescued: handed to an NGO
    waste_bin_g: int  # waste: composted or binned (the only waste of the KPI, M6 §3.5)
    waste_kitchen_g: int  # rescued: taken by a kitchen partner
    waste_imperfect_g: int
    cogs_paise: int | None
    surplus_offered_g: int
    surplus_taken_g: int
    data_source: str  # the hub's


def _grams(kg: Decimal) -> int:
    return int((kg * 1000).quantize(Decimal(1), ROUND_HALF_UP))


async def hub_day_facts(
    session: AsyncSession,
    *,
    date_from: date,
    date_to: date,
    hub_ids: list[uuid.UUID] | None = None,
) -> list[HubDayFact]:
    """Hub-days between the two IST dates (inclusive) that have packing tasks or waste logs."""
    days = (
        select(PackingTask.hub_id, PackingTask.delivery_date)
        .where(PackingTask.delivery_date >= date_from, PackingTask.delivery_date <= date_to)
        .group_by(PackingTask.hub_id, PackingTask.delivery_date)
    )
    if hub_ids is not None:
        days = days.where(PackingTask.hub_id.in_(hub_ids))
    found: set[tuple[uuid.UUID, date]] = {
        (hub_id, on) for hub_id, on in await session.execute(days)
    }

    ist_day = cast(func.timezone("Asia/Kolkata", WasteLog.at), Date)
    logs = (
        select(WasteLog.hub_id, ist_day, WasteLog.cascade_to, func.sum(WasteLog.kg))
        .where(
            WasteLog.at >= datetime.combine(date_from, time(), IST),
            WasteLog.at < datetime.combine(date_to + timedelta(days=1), time(), IST),
        )
        .group_by(WasteLog.hub_id, ist_day, WasteLog.cascade_to)
    )
    if hub_ids is not None:
        logs = logs.where(WasteLog.hub_id.in_(hub_ids))
    waste: dict[tuple[uuid.UUID, date], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for hub_id, on, cascade_to, kg in await session.execute(logs):
        found.add((hub_id, on))
        if kg is not None:  # None: no log of the group has a weight
            waste[(hub_id, on)][cascade_to] += _grams(kg)
    if not found:
        return []

    sources = dict(
        (
            await session.execute(
                select(Hub.id, Hub.data_source).where(Hub.id.in_({hub_id for hub_id, _ in found}))
            )
        ).all()
    )
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
            waste_ngo_g=waste[(hub_id, on)]["ngo"],
            waste_bin_g=waste[(hub_id, on)]["bin"],
            waste_kitchen_g=waste[(hub_id, on)]["kitchen"],
            waste_imperfect_g=0,  # Imperfect Sabzi is not in v1
            cogs_paise=None,
            surplus_offered_g=0,
            surplus_taken_g=0,
            data_source=sources[hub_id],
        )
        for hub_id, on in sorted(found, key=lambda key: (key[1], str(key[0])))
    ]
