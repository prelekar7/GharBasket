"""Kept items and returned bags (spec §3.5, ADR-0027 §2, M4 §3.8).

After a box's second miss M4 carries its non-perishable lines (`orders.api.carried_lines`). When
the returned bag is scanned at the hub, those lines become **kept items** of the household, on a
shelf apart from stock, and everything else in the bag becomes grade C **returned lots**, which
the 12:30 run offers on (a returned bag is never packed for another customer). A kept item stays
`kept` until a box carries it (`packed`, M6 §3.2), M4's 14 days pass (`released` into a returned
lot) or the hub manager loses it (`lost`, refunded by M4).
"""

import re
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.community import api as community_api
from gharbasket.modules.hub_ops import packing, procurement, receiving, stock
from gharbasket.modules.hub_ops.models import (
    InventoryLot,
    KeptItem,
    PackingTask,
    ReturnedBag,
    StockMove,
)
from gharbasket.modules.hub_ops.schemas import (
    KeptItemOut,
    PackingSociety,
    PackingStep,
    ReturnedBagOut,
)
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.orders import api as orders_api
from gharbasket.platform import audit
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.locale import Language, localised

BAG_QR = re.compile(r"^GB1:([0-9a-fA-F]{32})$")


def parse_bag_qr(code: str) -> uuid.UUID | None:
    """The order id on a bag label (`packing.bag_qr`), or None if it is not one."""
    found = BAG_QR.match(code.strip())
    return None if found is None else uuid.UUID(found.group(1))


def bag_unknown() -> ProblemError:
    return ProblemError(
        404, "hub_ops.bag_unknown", "This is not a packed box or add-on bag of this hub."
    )


def bag_not_returned(status: str) -> ProblemError:
    return ProblemError(
        409,
        "hub_ops.bag_not_returned",
        "This bag is not back after a second miss; nothing is unpacked.",
        extra={"current_status": status},
    )


def item_not_found() -> ProblemError:
    return ProblemError(404, "hub_ops.kept_item_not_found", "No such kept item.")


def item_settled(status: str) -> ProblemError:
    return ProblemError(
        409,
        "hub_ops.kept_item_settled",
        "This kept item is already packed or released.",
        extra={"current_status": status},
    )


# --- reading -------------------------------------------------------------------------------------


def _flat(towers: dict[str, str], tower_id: str | None, flat: str | None) -> str | None:
    if flat is None:
        return None
    tower = towers.get(tower_id or "")
    return f"{tower}, {flat}" if tower else flat


async def items_out(
    session: AsyncSession, items: list[KeptItem], lang: Language
) -> list[KeptItemOut]:
    """Kept items as the API shows them, by society name, then flat (digits as numbers)."""
    societies = await community_api.society_labels(session, list({i.society_id for i in items}))
    products = await catalog_api.line_products(session, list({i.product_id for i in items}))

    def out(item: KeptItem) -> KeptItemOut:
        society, product = societies.get(item.society_id), products.get(item.product_id)
        return KeptItemOut(
            id=item.id,
            carry_id=item.carry_id,
            from_order_id=item.from_order_id,
            society=PackingSociety(id=item.society_id, name=society.name if society else ""),
            flat_label=_flat(society.towers if society else {}, item.tower_id, item.flat),
            product_id=item.product_id,
            slug=product.slug if product else "",
            name=localised(product.names, lang) if product else "",
            step=PackingStep(
                qty=item.step_qty,
                unit=item.step_unit,
                label=catalog_api.step_label(item.step_qty, item.step_unit, lang),
            ),
            qty=item.qty,
            status=item.status,  # pyright: ignore[reportArgumentType]
            kept_at=item.kept_at,
            packed_task_id=item.packed_task_id,
            settled_at=item.settled_at,
        )

    shown = [out(item) for item in items]
    return sorted(
        shown,
        key=lambda k: (
            k.society.name.casefold(),
            packing._natural(k.flat_label or ""),  # pyright: ignore[reportPrivateUsage]
            k.kept_at,
            k.id,
        ),
    )


async def list_items(
    session: AsyncSession, hub_id: uuid.UUID, status: str | None, lang: Language
) -> list[KeptItemOut]:
    stmt = select(KeptItem).where(KeptItem.hub_id == hub_id)
    if status is not None:
        stmt = stmt.where(KeptItem.status == status)
    return await items_out(session, list(await session.scalars(stmt)), lang)


async def get_item(
    session: AsyncSession, hub_id: uuid.UUID, item_id: uuid.UUID, *, for_update: bool = False
) -> KeptItem:
    stmt = select(KeptItem).where(KeptItem.id == item_id, KeptItem.hub_id == hub_id)
    item = await session.scalar(stmt.with_for_update() if for_update else stmt)
    if item is None:
        raise item_not_found()
    return item


# --- lots from returned goods --------------------------------------------------------------------


async def _returned_lot(
    session: AsyncSession,
    hub_id: uuid.UUID,
    product_id: uuid.UUID,
    qty: Decimal,
    unit: str,
    *,
    order_id: uuid.UUID,
    best_before: datetime,
    ref_id: uuid.UUID,
    kept_item_id: uuid.UUID | None = None,
    by_user_id: uuid.UUID | None = None,
) -> InventoryLot:
    """A grade C lot at no cost from goods that came back, and its `return` move."""
    lot = InventoryLot(
        hub_id=hub_id,
        product_id=product_id,
        unit=unit,
        source="returned",
        order_id=order_id,
        grade="C",
        qty_in=qty,
        qty_on_hand=Decimal(0),
        unit_cost_paise=None,
        received_at=datetime.now(UTC),
        best_before=best_before,
    )
    session.add(lot)
    await session.flush()
    await receiving.move(
        session,
        hub_id=hub_id,
        product_id=product_id,
        unit=unit,
        kind="return",
        ref_id=ref_id,
        qty=qty,
        lot=lot,
        by_user_id=by_user_id,
        kept_item_id=kept_item_id,
    )
    return lot


def _best_before(packed_at: datetime | None, shelf_life_h: int | None) -> datetime:
    """The bag's packing time plus the product's shelf life (a year without one)."""
    start = packed_at or datetime.now(UTC)
    return start + (
        timedelta(hours=shelf_life_h) if shelf_life_h is not None else receiving.NO_SHELF_LIFE
    )


async def release(
    session: AsyncSession, items: list[KeptItem], by_user_id: uuid.UUID | None = None
) -> int:
    """Kept items that nobody carried become returned lots for the next 12:30 run (M4's 14 days
    are over, or the account is erased). Only `kept` items change; the count is those."""
    kept = [i for i in items if i.status == "kept"]
    if not kept:
        return 0
    products = await catalog_api.stock_products(session, list({i.product_id for i in kept}))
    for item in kept:
        task = await packing.bag_for_order(session, item.bag_order_id)
        product = products[item.product_id]
        unit = procurement.OPS_UNIT[item.step_unit][0]
        await _returned_lot(
            session,
            item.hub_id,
            item.product_id,
            procurement.to_ops(item.qty, item.step_qty, item.step_unit),
            unit,
            order_id=item.bag_order_id,
            best_before=_best_before(task.packed_at if task else None, product.shelf_life_h),
            ref_id=item.id,
            kept_item_id=item.id,
            by_user_id=by_user_id,
        )
        item.status, item.settled_at = "released", datetime.now(UTC)
    await session.flush()
    return len(kept)


# --- the returned bag ----------------------------------------------------------------------------


async def _answer(
    session: AsyncSession, task: PackingTask, order: orders_api.Order, lang: Language
) -> ReturnedBagOut:
    """The scan's result: the bag's kept items and the lots its other goods became."""
    items = list(await session.scalars(select(KeptItem).where(KeptItem.bag_order_id == order.id)))
    lots = list(
        await session.scalars(
            select(InventoryLot)
            .join(StockMove, StockMove.lot_id == InventoryLot.id)
            .where(StockMove.kind == "return", StockMove.ref_id == order.id)
            .order_by(InventoryLot.id)
        )
    )
    societies = await community_api.society_labels(session, [order.society_id])
    society = societies.get(order.society_id)
    return ReturnedBagOut(
        order_id=order.id,
        society=PackingSociety(id=order.society_id, name=society.name if society else ""),
        flat_label=_flat(society.towers if society else {}, task.tower_id, task.flat),
        kept=await items_out(session, items, lang),
        lots=await stock.lots_out(session, lots, lang) if lots else [],
    )


async def scan(
    session: AsyncSession, hub_id: uuid.UUID, code: str, by_user_id: uuid.UUID, lang: Language
) -> ReturnedBagOut:
    """Unpack a returned bag, once. The order must be this hub's box or add-on order with a packed
    task (else 404 `hub_ops.bag_unknown`) that M4 refunded after its second miss (else 409
    `hub_ops.bag_not_returned`, with the order's `current_status`: a bag after a first miss waits
    for the next van and nothing is unpacked). A second scan answers the first scan's result."""
    order_id = parse_bag_qr(code)
    task = None if order_id is None else await packing.bag_for_order(session, order_id)
    if order_id is None or task is None or task.hub_id != hub_id or task.status != "packed":
        raise bag_unknown()
    order = await orders_api.get(session, order_id, for_update=True)
    if order.status != "refunded":
        raise bag_not_returned(order.status)
    first = await session.execute(
        insert(ReturnedBag)
        .values(order_id=order_id, hub_id=hub_id, scanned_by=by_user_id)
        .on_conflict_do_nothing()
        .returning(ReturnedBag.order_id)
    )
    if first.first() is None:
        return await _answer(session, task, order, lang)

    carried = await orders_api.carried_lines(session, order_id)
    label = (await identity_api.delivery_labels(session, [order.household_id])).get(
        order.household_id
    )
    tower_id, flat = (label.tower_id, label.flat) if label else (task.tower_id, task.flat)
    for line in carried:
        if await session.scalar(
            select(KeptItem.id).where(KeptItem.carry_id == line.carry_id, KeptItem.status == "kept")
        ):
            continue  # already on the shelf from another bag: one item per carry
        session.add(
            KeptItem(
                hub_id=hub_id,
                household_id=order.household_id,
                carry_id=line.carry_id,
                from_order_id=line.carried_from_order_id,
                bag_order_id=order_id,
                society_id=order.society_id,
                tower_id=tower_id,
                flat=flat,
                product_id=line.product_id,
                step_qty=line.step_qty,
                step_unit=line.step_unit,
                qty=line.steps,
            )
        )
    await session.flush()
    await _unpack_the_rest(session, hub_id, task, order, carried, by_user_id)
    return await _answer(session, task, order, lang)


async def _unpack_the_rest(
    session: AsyncSession,
    hub_id: uuid.UUID,
    task: PackingTask,
    order: orders_api.Order,
    carried: list[orders_api.CarriedLine],
    by_user_id: uuid.UUID,
) -> None:
    """One returned lot per product for what the bag held besides the household's kept items: the
    task's picked steps (the substitute's, when the line was substituted) less the steps M4
    carried from this order."""
    [summary] = await orders_api.summaries(session, [order.id])
    on_order = {line.product_id: line for line in summary.lines}
    kept_steps = Counter[uuid.UUID]()
    for line in carried:
        if line.carried_from_order_id == order.id:
            kept_steps[line.product_id] += line.steps
    held: dict[uuid.UUID, tuple[int, int, str]] = {}  # product -> (steps, step_qty, step_unit)
    for line in await packing.lines_of(session, task.id):
        product_id = line.substitute_product_id or line.product_id
        ordered = on_order.get(product_id)
        step_qty, step_unit = (
            (ordered.step_qty, ordered.step_unit) if ordered else (line.step_qty, line.step_unit)
        )
        steps = held.get(product_id, (0, step_qty, step_unit))[0] + line.picked_qty
        held[product_id] = (steps, step_qty, step_unit)
    products = await catalog_api.stock_products(session, list(held))
    for product_id, (steps, step_qty, step_unit) in held.items():
        left = steps - kept_steps[product_id]
        if left <= 0 or product_id not in products:
            continue
        await _returned_lot(
            session,
            hub_id,
            product_id,
            procurement.to_ops(left, step_qty, step_unit),
            procurement.OPS_UNIT[step_unit][0],
            order_id=order.id,
            best_before=_best_before(task.packed_at, products[product_id].shelf_life_h),
            ref_id=order.id,
            by_user_id=by_user_id,
        )


# --- a lost kept item ----------------------------------------------------------------------------


async def lose(session: AsyncSession, item: KeptItem, note: str, by_user_id: uuid.UUID) -> KeptItem:
    """The hub manager reports a kept item lost: M4 refunds it at its paid price, no fee
    (`orders.api.refund_carry`, which answers 409 `orders.carry_not_refundable` once a box has
    it), and the item becomes `lost`. A lost item returns as is; a packed or released one is 409
    `hub_ops.kept_item_settled`. The row was locked by the caller."""
    if item.status == "lost":
        return item
    if item.status != "kept":
        raise item_settled(item.status)
    await orders_api.refund_carry(session, item.carry_id, by_user_id)
    item.status, item.settled_at = "lost", datetime.now(UTC)
    await session.flush()
    after: dict[str, Any] = {"status": "lost", "carry_id": str(item.carry_id), "note": note}
    await audit.record(
        session,
        actor_user_id=by_user_id,
        action="kept_items.lost",
        entity_type="kept_item",
        entity_id=str(item.id),
        before={"status": "kept"},
        after=after,
    )
    return item


async def release_household(session: AsyncSession, household_id: uuid.UUID) -> tuple[int, int]:
    """M1's erase hook: the household's items still on the shelf become returned lots, and the
    flat copied from the address goes from all of its items. Returns (released, scrubbed)."""
    items = list(
        await session.scalars(
            select(KeptItem).where(KeptItem.household_id == household_id).with_for_update()
        )
    )
    released = await release(session, items)
    scrubbed = 0
    for item in items:
        if item.flat is not None or item.tower_id is not None:
            item.tower_id = item.flat = None
            scrubbed += 1
    await session.flush()
    return released, scrubbed
