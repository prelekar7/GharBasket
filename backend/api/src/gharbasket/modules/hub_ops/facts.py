"""Fact rows of M6 for M12's analytics refresh (M12 §3.1): one row per hub and day that has packing
tasks (by delivery date), waste logs (by the IST day they were logged) or surplus offers, with
grams and paise only.

- **Waste** (`waste_*_g`) is the `kg` of the hub's waste logs of that IST day, by `cascade_to`, in
  grams; a log without a weight is left out, as in M6 §3.5's summary. Only `bin` is waste for the
  KPI (M6 §3.5); `kitchen` and `ngo` are food rescued and kept apart.

- **Surplus** (M6 §3.5's cascade), weighed like waste logs (`kg`/`l` as themselves, else the
  catalog's `unit_g`; an offer of unknown weight is left out):
  `surplus_offered_g` is the stock that entered the cascade that IST day: offers with no
  `previous_offer_id` (the kitchen offers), by `created_at`, so a quantity that moves on from a
  kitchen to an NGO is counted once. `surplus_taken_g` is what kitchens and NGOs took, the
  `taken_qty` of `taken` offers by the IST day of `settled_at` (the day of the waste log it writes,
  so it matches the surplus part of `waste_kitchen_g + waste_ngo_g`). Both are 0 without offers.

- **COGS** (`cogs_paise`, M12 §3.4) is the cost of the paid lines packed for the day: the `pack`
  moves the 12:30 draw-down wrote for the day's packed tasks, each at its lot's
  `unit_cost_paise`, summed in paise (half up per move). It is None unless every packed task of
  the day is drawn, has moves, and every move came from a costed lot: an unlotted rest (packed
  without stock the system knew of), a lot received without a cost or a task not drawn yet would
  make a partial sum that reads as complete, and `margin_bp` must not do that.

What M6 cannot say is returned as 0 or None, never guessed: **Imperfect Sabzi**
(`waste_imperfect_g`) is 0, it is not in v1.
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
from gharbasket.modules.hub_ops import waste
from gharbasket.modules.hub_ops.models import (
    Hub,
    InventoryLot,
    PackingLine,
    PackingTask,
    StockMove,
    SurplusOffer,
    WasteLog,
)

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


async def _surplus_grams(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    hub_ids: list[uuid.UUID] | None,
) -> tuple[dict[tuple[uuid.UUID, date], int], dict[tuple[uuid.UUID, date], int]]:
    """Grams of surplus offered and taken by (hub, IST day): see the module docstring."""
    start = datetime.combine(date_from, time(), IST)
    end = datetime.combine(date_to + timedelta(days=1), time(), IST)
    offered_day = cast(func.timezone("Asia/Kolkata", SurplusOffer.created_at), Date)
    taken_day = cast(func.timezone("Asia/Kolkata", SurplusOffer.settled_at), Date)
    offered = (
        select(SurplusOffer.hub_id, offered_day, SurplusOffer.product_id, SurplusOffer.unit)
        .add_columns(func.sum(SurplusOffer.qty))
        .where(
            SurplusOffer.previous_offer_id.is_(None),
            SurplusOffer.created_at >= start,
            SurplusOffer.created_at < end,
        )
        .group_by(SurplusOffer.hub_id, offered_day, SurplusOffer.product_id, SurplusOffer.unit)
    )
    taken = (
        select(SurplusOffer.hub_id, taken_day, SurplusOffer.product_id, SurplusOffer.unit)
        .add_columns(func.sum(SurplusOffer.taken_qty))
        .where(
            SurplusOffer.status == "taken",
            SurplusOffer.settled_at >= start,
            SurplusOffer.settled_at < end,
        )
        .group_by(SurplusOffer.hub_id, taken_day, SurplusOffer.product_id, SurplusOffer.unit)
    )
    if hub_ids is not None:
        offered = offered.where(SurplusOffer.hub_id.in_(hub_ids))
        taken = taken.where(SurplusOffer.hub_id.in_(hub_ids))
    rows = [(r, True) for r in (await session.execute(offered)).all()]
    rows += [(r, False) for r in (await session.execute(taken)).all()]
    products = await catalog_api.stock_products(session, list({r[2] for r, _ in rows}))
    totals: tuple[dict[tuple[uuid.UUID, date], int], dict[tuple[uuid.UUID, date], int]] = (
        defaultdict(int),
        defaultdict(int),
    )
    for (hub_id, on, product_id, unit, qty), is_offered in rows:
        product = products.get(product_id)
        kg = waste.weigh(qty or Decimal(0), unit, None if product is None else product.unit_g)
        if kg is not None:  # unknown weight: left out, as in the waste logs
            totals[0 if is_offered else 1][(hub_id, on)] += _grams(kg)
    return totals


async def _cogs(
    session: AsyncSession,
    date_from: date,
    date_to: date,
    hub_ids: list[uuid.UUID] | None,
) -> dict[tuple[uuid.UUID, date], int]:
    """COGS in paise by (hub, delivery date) for the days where every packed task is drawn and
    every `pack` move of them came from a lot with a cost; the other days are absent."""
    tasks = select(
        PackingTask.id, PackingTask.hub_id, PackingTask.delivery_date, PackingTask.drawn_at
    )
    tasks = tasks.where(
        PackingTask.status == "packed",
        PackingTask.delivery_date >= date_from,
        PackingTask.delivery_date <= date_to,
    )
    if hub_ids is not None:
        tasks = tasks.where(PackingTask.hub_id.in_(hub_ids))
    day_of: dict[uuid.UUID, tuple[uuid.UUID, date]] = {}
    complete: dict[tuple[uuid.UUID, date], bool] = {}
    for task_id, hub_id, on, drawn_at in (await session.execute(tasks)).all():
        day_of[task_id] = (hub_id, on)
        complete[(hub_id, on)] = complete.get((hub_id, on), True) and drawn_at is not None
    moves = await session.execute(
        select(StockMove.ref_id, StockMove.qty, InventoryLot.unit_cost_paise)
        .join(InventoryLot, InventoryLot.id == StockMove.lot_id, isouter=True)
        .where(StockMove.kind == "pack", StockMove.ref_id.in_(list(day_of)))
    )
    cogs: dict[tuple[uuid.UUID, date], int] = defaultdict(int)
    for ref_id, qty, unit_cost in moves.all():
        key = day_of[ref_id]
        if unit_cost is None:  # unlotted, or a lot received without a cost
            complete[key] = False
        else:
            cogs[key] += int((-qty * unit_cost).quantize(Decimal(1), ROUND_HALF_UP))
    return {key: cogs[key] for key, ok in complete.items() if ok and key in cogs}


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
    offered_g, taken_g = await _surplus_grams(session, date_from, date_to, hub_ids)
    found |= offered_g.keys() | taken_g.keys()  # a day of surplus is a hub-day too
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
    cogs = await _cogs(session, date_from, date_to, hub_ids)
    return [
        HubDayFact(
            hub_id=hub_id,
            on=on,
            packed_g=round(grams[(hub_id, on)]),
            waste_ngo_g=waste[(hub_id, on)]["ngo"],
            waste_bin_g=waste[(hub_id, on)]["bin"],
            waste_kitchen_g=waste[(hub_id, on)]["kitchen"],
            waste_imperfect_g=0,  # Imperfect Sabzi is not in v1
            cogs_paise=cogs.get((hub_id, on)),
            surplus_offered_g=offered_g[(hub_id, on)],
            surplus_taken_g=taken_g[(hub_id, on)],
            data_source=sources[hub_id],
        )
        for hub_id, on in sorted(found, key=lambda key: (key[1], str(key[0])))
    ]
