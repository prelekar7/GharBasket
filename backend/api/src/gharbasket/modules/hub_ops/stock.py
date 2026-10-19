"""Stock (spec §3.4): lots, the hub's inventory view and physical counts. Every change to a lot is
a stock move written by `receiving.move`, so a lot's `qty_on_hand` is the sum of its moves.
"""

import uuid
from collections import defaultdict
from datetime import UTC, datetime

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.hub_ops import procurement, receiving
from gharbasket.modules.hub_ops.models import (
    GoodsReceipt,
    GoodsReceiptLine,
    InventoryLot,
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
    """Lots with stock by product, first expiry first. Packing is drawn down by the 12:30 run
    (§3.5), so until that run exists nothing is packed and not drawn."""
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
    items = [
        InventoryItem(
            product_id=p,
            slug=products[p].slug,
            name=product_lots[0].name,
            unit=product_lots[0].unit,
            shelf_life_h=products[p].shelf_life_h,
            on_hand_qty=float(sum(x.qty_on_hand for x in lots if x.product_id == p)),
            packed_not_drawn_qty=0.0,
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
