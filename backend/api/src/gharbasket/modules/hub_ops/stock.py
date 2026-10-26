"""Stock (spec §3.4): lots, the hub's inventory view, physical counts and the daily draw-down of
packing. Every change to a lot is a stock move written by `receiving.move`, so a lot's
`qty_on_hand` is the sum of its moves.
"""

import uuid
from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi.encoders import jsonable_encoder
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.hub_ops import procurement, receiving
from gharbasket.modules.hub_ops.models import (
    GoodsReceipt,
    GoodsReceiptLine,
    InventoryLot,
    PackingLine,
    PackingTask,
    StockMove,
    Vendor,
)
from gharbasket.modules.hub_ops.schemas import (
    CountIn,
    InventoryItem,
    InventoryOut,
    LotDetailOut,
    LotOut,
    Ref,
    StockMoveOut,
    UserRef,
)
from gharbasket.modules.identity import api as identity_api
from gharbasket.platform import audit
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.locale import Language, localised


def lot_not_found() -> ProblemError:
    return ProblemError(404, "hub_ops.lot_not_found", "No such lot.")


async def get_lot(
    session: AsyncSession, lot_id: uuid.UUID, *, for_update: bool = False
) -> InventoryLot:
    stmt = select(InventoryLot).where(InventoryLot.id == lot_id)
    lot = await session.scalar(
        stmt.with_for_update().execution_options(populate_existing=True) if for_update else stmt
    )
    if lot is None:
        raise lot_not_found()
    return lot


async def lots_out(session: AsyncSession, lots: list[InventoryLot], lang: Language) -> list[LotOut]:
    products = await catalog_api.stock_products(session, list({x.product_id for x in lots}))
    sources = {
        line_id: (receipt_id, Ref(id=vendor_id, name=name))
        for line_id, receipt_id, vendor_id, name in await session.execute(
            select(GoodsReceiptLine.id, GoodsReceipt.id, Vendor.id, Vendor.name)
            .join(GoodsReceipt, GoodsReceipt.id == GoodsReceiptLine.receipt_id)
            .join(Vendor, Vendor.id == GoodsReceipt.vendor_id)
            .where(GoodsReceiptLine.id.in_({x.receipt_line_id for x in lots}))
        )
    }

    def out(lot: InventoryLot) -> LotOut:
        product = products[lot.product_id]
        receipt_id, vendor = (None, None)
        if lot.receipt_line_id is not None:
            receipt_id, vendor = sources[lot.receipt_line_id]
        return LotOut(
            **lot.as_dict(),
            slug=product.slug,
            name=localised(product.names, lang),
            receipt_id=receipt_id,
            vendor=vendor,
        )

    return [out(lot) for lot in lots]


async def lot_detail(session: AsyncSession, lot: InventoryLot, lang: Language) -> LotDetailOut:
    moves = list(
        await session.scalars(
            select(StockMove).where(StockMove.lot_id == lot.id).order_by(StockMove.at, StockMove.id)
        )
    )
    users = await identity_api.user_contacts(
        session, [m.by_user_id for m in moves if m.by_user_id is not None]
    )

    def by(user_id: uuid.UUID | None) -> UserRef | None:
        if user_id is None:
            return None
        user = users.get(user_id)
        return UserRef(user_id=user_id, name=None if user is None else user.name)

    lot_out = (await lots_out(session, [lot], lang))[0]
    return LotDetailOut(
        **lot_out.model_dump(),
        moves=[StockMoveOut(**m.as_dict(), by=by(m.by_user_id)) for m in moves],
    )


async def inventory(
    session: AsyncSession,
    hub_id: uuid.UUID,
    lang: Language,
    *,
    product_id: uuid.UUID | None = None,
    expiring_before: datetime | None = None,
) -> InventoryOut:
    """Lots with stock by product, first expiry first, each with what is packed and not yet
    drawn down by the 12:30 run (`packed_not_drawn_qty`, paid lines only)."""
    stmt = select(InventoryLot).where(InventoryLot.hub_id == hub_id, InventoryLot.qty_on_hand > 0)
    if product_id is not None:
        stmt = stmt.where(InventoryLot.product_id == product_id)
    if expiring_before is not None:
        stmt = stmt.where(InventoryLot.best_before < expiring_before)
    lots = list(
        await session.scalars(
            stmt.order_by(InventoryLot.best_before, InventoryLot.received_at, InventoryLot.id)
        )
    )
    by_product: dict[uuid.UUID, list[LotOut]] = defaultdict(list)
    for lot in await lots_out(session, lots, lang):
        by_product[lot.product_id].append(lot)
    products = await catalog_api.stock_products(session, list(by_product))
    undrawn: dict[uuid.UUID, Decimal] = defaultdict(Decimal)
    for picked in (await _picked(session, _undrawn(hub_id))).values():
        for product, (qty, _) in picked.items():
            undrawn[product] += qty
    items = [
        InventoryItem(
            product_id=p,
            slug=products[p].slug,
            name=product_lots[0].name,
            unit=product_lots[0].unit,
            shelf_life_h=products[p].shelf_life_h,
            on_hand_qty=float(sum(x.qty_on_hand for x in lots if x.product_id == p)),
            packed_not_drawn_qty=float(undrawn.get(p, 0)),
            lots=product_lots,
        )
        for p, product_lots in by_product.items()
    ]
    items.sort(key=lambda x: (products[x.product_id].shelf, x.name))
    return InventoryOut(as_of=datetime.now(UTC), items=items)


async def count(
    session: AsyncSession, lot: InventoryLot, body: CountIn, actor_user_id: uuid.UUID
) -> None:
    """Set a row-locked lot's stock after a physical count: a `count` move for the difference
    (none when it matches) and an audit entry either way."""
    qty = procurement.check_qty(body.qty_on_hand, lot.unit)
    before = lot.qty_on_hand
    if qty != before:
        await receiving.move(
            session,
            hub_id=lot.hub_id,
            product_id=lot.product_id,
            unit=lot.unit,
            kind="count",
            ref_id=uuid.uuid4(),  # each count is its own source
            qty=qty - before,
            lot=lot,
            reason=body.reason,
            by_user_id=actor_user_id,
        )
    await audit.record(
        session,
        actor_user_id=actor_user_id,
        action="inventory.counted",
        entity_type="inventory_lot",
        entity_id=str(lot.id),
        before=jsonable_encoder({"qty_on_hand": before}),
        after=jsonable_encoder({"qty_on_hand": qty, "reason": body.reason}),
    )


# --- the draw-down (§3.4, the 12:30 run's first step) ---------------------------------------------

DRAWN_GRADES = ("A", "B")  # grade C is never packed (§3.4)


def _undrawn(hub_id: uuid.UUID, until: date | None = None) -> Select[PackingTask]:
    """The hub's packed tasks not drawn down yet (of delivery dates up to `until`)."""
    stmt = select(PackingTask).where(
        PackingTask.hub_id == hub_id,
        PackingTask.status == "packed",
        PackingTask.drawn_at.is_(None),
    )
    return stmt if until is None else stmt.where(PackingTask.delivery_date <= until)


async def _picked(
    session: AsyncSession, tasks: Select[PackingTask]
) -> dict[uuid.UUID, dict[uuid.UUID, tuple[Decimal, str]]]:
    """By task, then by the product in the bag (the substitute when there is one): the picked
    steps of the paid lines in the operations unit, and that unit. Carried lines are left out:
    `packing.draw_carried` moved them at `complete`. A substitute's steps are its own (the
    packing line keeps the ordered product's step)."""
    task_ids = tasks.with_only_columns(PackingTask.id).scalar_subquery()
    lines = (
        await session.execute(
            select(
                PackingLine.task_id,
                PackingLine.product_id,
                PackingLine.substitute_product_id,
                PackingLine.step_qty,
                PackingLine.step_unit,
                PackingLine.picked_qty,
            ).where(
                PackingLine.task_id.in_(task_ids),
                PackingLine.carried.is_(False),
                PackingLine.picked_qty > 0,
            )
        )
    ).all()
    substitutes = await catalog_api.stock_products(
        session, list({x.substitute_product_id for x in lines if x.substitute_product_id})
    )
    found: dict[uuid.UUID, dict[uuid.UUID, tuple[Decimal, str]]] = defaultdict(dict)
    for task_id, product_id, substitute_id, step_qty, step_unit, picked in lines:
        if substitute_id is not None:
            product_id = substitute_id
            if (product := substitutes.get(substitute_id)) is not None:
                step_qty, step_unit = product.step_qty, product.step_unit
        unit = procurement.OPS_UNIT[step_unit][0]
        qty = procurement.to_ops(picked, step_qty, step_unit)
        before, _ = found[task_id].get(product_id, (Decimal(0), unit))
        found[task_id][product_id] = (before + qty, unit)
    return found


async def drawn_picks(
    session: AsyncSession, hub_id: uuid.UUID, day: date
) -> dict[tuple[uuid.UUID, str], Decimal]:
    """What the day's packed tasks that were already drawn down took off the shelves, by
    (product in the bag, operations unit): shortages (§3.6) add it back to the lots on hand to
    get the stock the day started with."""
    drawn = select(PackingTask).where(
        PackingTask.hub_id == hub_id,
        PackingTask.delivery_date == day,
        PackingTask.status == "packed",
        PackingTask.drawn_at.is_not(None),
    )
    totals: dict[tuple[uuid.UUID, str], Decimal] = defaultdict(Decimal)
    for by_product in (await _picked(session, drawn)).values():
        for product_id, (qty, unit) in by_product.items():
            totals[(product_id, unit)] += qty
    return dict(totals)


async def draw_down(session: AsyncSession, hub_id: uuid.UUID, today: date, now: datetime) -> int:
    """Draw every packed task of the hub not drawn yet (delivery dates up to `today`) down from
    stock: for each product in the bag, `pack` moves from its grade A and B lots with stock, first
    expiry first out (best_before, received_at, id), and what no lot covers as one unlotted `pack`
    move; then `drawn_at`. The tasks are row-locked, then the lots by id (the caller holds the
    hub), and the moves are keyed by the task, so the draw never runs twice. Returns the tasks
    drawn."""
    undrawn = _undrawn(hub_id, today)
    tasks = list(
        await session.scalars(
            undrawn.order_by(PackingTask.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    if not tasks:
        return 0
    picked = await _picked(session, undrawn)
    wanted = {product for lines in picked.values() for product in lines}
    lots = await session.scalars(
        select(InventoryLot)
        .where(
            InventoryLot.hub_id == hub_id,
            InventoryLot.product_id.in_(wanted),
            InventoryLot.grade.in_(DRAWN_GRADES),
            InventoryLot.qty_on_hand > 0,
        )
        .order_by(InventoryLot.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    fefo: dict[tuple[uuid.UUID, str], list[InventoryLot]] = defaultdict(list)
    for lot in sorted(lots, key=lambda x: (x.best_before, x.received_at, x.id)):
        fefo[(lot.product_id, lot.unit)].append(lot)
    for task in sorted(tasks, key=lambda x: (x.packed_at or now, x.id)):
        for product_id, (qty, unit) in sorted(picked.get(task.id, {}).items()):
            rest = qty
            for lot in fefo[(product_id, unit)]:
                take = min(rest, lot.qty_on_hand)
                if take <= 0:
                    continue
                await receiving.move(
                    session,
                    hub_id=hub_id,
                    product_id=product_id,
                    unit=unit,
                    kind="pack",
                    ref_id=task.id,
                    qty=-take,
                    lot=lot,
                )
                rest -= take
                if rest == 0:
                    break
            if rest > 0:  # unlotted: packed without stock the system knew of
                await receiving.move(
                    session,
                    hub_id=hub_id,
                    product_id=product_id,
                    unit=unit,
                    kind="pack",
                    ref_id=task.id,
                    qty=-rest,
                )
        task.drawn_at = now
    await session.flush()
    return len(tasks)
