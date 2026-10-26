"""Shortages today (spec §3.6): the products the hub cannot cover for a date's packing lines, the
bags the gap falls on and what each family's rule allows there. A read only: the packer still
records a shortage bag by bag (§3.2), whose checks apply again at that moment.
"""

import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, time
from decimal import ROUND_FLOOR, Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.community import api as community_api
from gharbasket.modules.hub_ops import packing, procurement, stock
from gharbasket.modules.hub_ops.models import InventoryLot, PackingLine, PackingTask
from gharbasket.modules.hub_ops.schemas import (
    PackingStep,
    PackingSubstitute,
    ShortagesOut,
    ShortBag,
    ShortItem,
)
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.orders import api as orders_api
from gharbasket.platform.locale import Language, localised

Step = tuple[int, str]  # (step_qty, step_unit)


def _steps(ops_qty: Decimal, step: Step) -> int:
    """Whole steps in a quantity of the operations unit (§3.3's conversion)."""
    per = procurement.OPS_UNIT[step[1]][1]
    return int((ops_qty * per / step[0]).to_integral_value(ROUND_FLOOR))


async def _have(
    session: AsyncSession,
    hub_id: uuid.UUID,
    steps: dict[uuid.UUID, Step],
    cutoff: datetime,
    drawn: dict[tuple[uuid.UUID, str], Decimal],
) -> dict[uuid.UUID, int | None]:
    """Steps of each product the hub has: grade A and B lots on hand that are not past their
    best-before, plus what tasks already drawn down took (`drawn`). None when the step does not
    convert: its unit is unknown, or stock of the product is counted in another unit."""
    rows = await session.execute(
        select(InventoryLot.product_id, InventoryLot.unit, func.sum(InventoryLot.qty_on_hand))
        .where(
            InventoryLot.hub_id == hub_id,
            InventoryLot.product_id.in_(steps),
            InventoryLot.grade.in_(stock.DRAWN_GRADES),
            InventoryLot.qty_on_hand > 0,
            InventoryLot.best_before > cutoff,
        )
        .group_by(InventoryLot.product_id, InventoryLot.unit)
    )
    ops: dict[tuple[uuid.UUID, str], Decimal] = defaultdict(Decimal)
    for product_id, unit, qty in rows.all():
        ops[(product_id, unit)] += qty
    for key, qty in drawn.items():
        if key[0] in steps:
            ops[key] += qty
    have: dict[uuid.UUID, int | None] = {}
    for product_id, step in steps.items():
        known = procurement.OPS_UNIT.get(step[1])
        mine = {unit: qty for (p, unit), qty in ops.items() if p == product_id and qty > 0}
        if known is None or any(unit != known[0] for unit in mine):
            have[product_id] = None
        else:
            have[product_id] = _steps(mine.get(known[0], Decimal(0)), step)
    return have


async def read(session: AsyncSession, hub_id: uuid.UUID, day: date, lang: Language) -> ShortagesOut:
    now = datetime.now(UTC)
    tasks = list(
        await session.scalars(
            select(PackingTask)
            .where(PackingTask.hub_id == hub_id, PackingTask.delivery_date == day)
            .order_by(PackingTask.seq.desc())  # the end of the queue first
        )
    )
    lines: dict[uuid.UUID, list[PackingLine]] = defaultdict(list)
    if tasks:
        rows = await session.scalars(
            select(PackingLine).where(
                PackingLine.task_id.in_([t.id for t in tasks]), PackingLine.carried.is_(False)
            )
        )
        for line in rows:
            lines[line.task_id].append(line)

    needed: dict[uuid.UUID, int] = defaultdict(int)
    step_of: dict[uuid.UUID, Step] = {}
    for task in tasks:
        for line in lines[task.id]:
            needed[line.product_id] += line.qty
            step_of.setdefault(line.product_id, (line.step_qty, line.step_unit))
    cutoff = now if day == now.astimezone(packing.IST).date() else _start_of(day)
    drawn = await stock.drawn_picks(session, hub_id, day)
    have = await _have(session, hub_id, step_of, cutoff, drawn)

    def is_reported(line: PackingLine) -> bool:
        return line.short_qty > 0 or line.substitute_product_id is not None

    short_by = {p: max(0, needed[p] - h) if (h := have[p]) is not None else 0 for p in step_of}
    reported_products = {ln.product_id for t in tasks for ln in lines[t.id] if is_reported(ln)}
    listed = sorted(p for p in step_of if short_by[p] > 0 or p in reported_products)
    if not listed:
        return ShortagesOut(hub_id=hub_id, date=day, as_of=now, items=[])

    # Which lines fall in the gap: open (nothing picked or short) lines, last bag first, until
    # their steps cover the gap, and which lines are already reported.
    open_at: dict[uuid.UUID, list[tuple[PackingTask, PackingLine]]] = {}
    reported_at: dict[uuid.UUID, list[tuple[PackingTask, PackingLine]]] = {}
    for product_id in listed:
        covered, chosen = 0, []
        for task in tasks:
            if covered >= short_by[product_id]:
                break
            if task.status == "packed":
                continue
            for line in lines[task.id]:
                free = line.qty - line.picked_qty - line.short_qty
                if (
                    line.product_id == product_id
                    and not is_reported(line)
                    and free > 0
                    and covered < short_by[product_id]
                ):
                    chosen.append((task, line))
                    covered += free
        open_at[product_id] = chosen
        reported_at[product_id] = [
            (task, line)
            for task in tasks
            for line in lines[task.id]
            if line.product_id == product_id and is_reported(line)
        ]

    shown = {t.id: t for p in listed for t, _ in open_at[p] + reported_at[p]}.values()
    summaries = {
        s.order.id: s for s in await orders_api.summaries(session, [t.order_id for t in shown])
    }
    prefs = await identity_api.shortage_preferences(
        session, {s.order.household_id for s in summaries.values()}
    )
    societies = await community_api.society_labels(session, list({t.society_id for t in shown}))

    # Substitutes: the catalog's list per short product, with today's prices and their stock.
    candidates = {
        p: await catalog_api.substitute_ids(session, p, hub_id) for p in listed if open_at[p]
    }
    packed_subs = {
        line.substitute_product_id
        for p in listed
        for _, line in reported_at[p]
        if line.substitute_product_id
    }
    wanted = set(listed) | packed_subs | {s for ids in candidates.values() for s in ids}
    products = await catalog_api.stock_products(session, list(wanted))
    prices = {
        p: lp.price_paise
        for p, lp in (await catalog_api.line_products(session, list(wanted), hub_id)).items()
    }
    sub_steps = {
        s: (products[s].step_qty, products[s].step_unit)
        for ids in candidates.values()
        for s in ids
        if s in products
    }
    left = await _have(session, hub_id, sub_steps, cutoff, drawn)

    def step_out(step: Step) -> PackingStep:
        return PackingStep(
            qty=step[0], unit=step[1], label=catalog_api.step_label(step[0], step[1], lang)
        )

    def substitute_out(product_id: uuid.UUID) -> PackingSubstitute | None:
        product = products.get(product_id)
        if product is None:
            return None
        return PackingSubstitute(
            product_id=product.id,
            slug=product.slug,
            name=localised(product.names, lang),
            step=step_out((product.step_qty, product.step_unit)),
        )

    def bag(
        task: PackingTask,
        line: PackingLine,
        qty: int,
        outcome: str,
        substitute: PackingSubstitute | None,
        refund: int,
    ) -> ShortBag:
        return ShortBag(
            task_id=task.id,
            bag_qr=packing.bag_qr(task.order_id),
            society=societies[task.society_id].name if task.society_id in societies else "",
            flat_label=packing.flat_label_of(task, societies.get(task.society_id)),
            qty=qty,
            outcome=outcome,  # pyright: ignore[reportArgumentType]
            substitute=substitute,
            refund_paise=refund,
        )

    def paid(task: PackingTask, line: PackingLine) -> int:
        """The unit price the household paid for the line (the substitute's, once swapped)."""
        found = next(
            (
                ln
                for ln in summaries[task.order_id].lines
                if line.product_id in (ln.product_id, ln.substituted_from)
            ),
            None,
        )
        return 0 if found is None else found.unit_price_paise

    def on_order(task: PackingTask) -> set[uuid.UUID]:
        out: set[uuid.UUID] = set()
        for ln in summaries[task.order_id].lines:
            out.add(ln.product_id)
            if ln.substituted_from is not None:
                out.add(ln.substituted_from)
        return out

    items: list[ShortItem] = []
    for product_id in listed:
        bags: list[ShortBag] = []
        for task, line in open_at[product_id]:
            price = paid(task, line)
            household = summaries[task.order_id].order.household_id
            qty = line.qty - line.picked_qty - line.short_qty
            outcome, substitute, refund = "refund", None, qty * price
            if line.picked_qty == 0 and prefs[household] != "refund":
                taken = on_order(task)
                for sid in candidates.get(product_id, []):
                    cost, room = prices.get(sid), left.get(sid)
                    if (
                        sid in taken
                        or cost is None
                        or cost > price
                        or room is None
                        or room < line.qty
                    ):
                        continue
                    found = substitute_out(sid)
                    if found is None:
                        continue
                    left[sid] = room - line.qty
                    outcome, substitute, qty = "substitute", found, line.qty
                    refund = line.qty * (price - cost)
                    break
            bags.append(bag(task, line, qty, outcome, substitute, refund))
        reported: list[ShortBag] = []
        for task, line in reported_at[product_id]:
            price = paid(task, line)
            if line.substitute_product_id is None:
                reported.append(
                    bag(task, line, line.short_qty, "refund", None, line.short_qty * price)
                )
                continue
            was = prices.get(line.product_id)
            refund = 0 if was is None else line.qty * max(0, was - price)
            reported.append(
                bag(
                    task,
                    line,
                    line.qty,
                    "substitute",
                    substitute_out(line.substitute_product_id),
                    refund,
                )
            )
        product = products.get(product_id)
        items.append(
            ShortItem(
                product_id=product_id,
                slug=product.slug if product else "",
                name=localised(product.names, lang) if product else "",
                step=step_out(step_of[product_id]),
                needed=needed[product_id],
                received=have[product_id],
                short_by=short_by[product_id],
                bags=bags,
                reported=reported,
            )
        )
    items.sort(key=lambda i: (-i.short_by, i.name.casefold(), str(i.product_id)))
    return ShortagesOut(hub_id=hub_id, date=day, as_of=now, items=items)


def _start_of(day: date) -> datetime:
    return datetime.combine(day, time(), packing.IST)
