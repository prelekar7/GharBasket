"""Goods receipt (spec §3.4): what a vendor delivered, weighed and graded at the dock at 04:00,
against a sent purchase order or ad hoc (a market buy). A receipt is a fact, never edited: each
accepted line becomes a lot with a best-before time, and every change to a lot is a stock move.

Rejected goods go back to the vendor: neither stock nor waste, they count only in vendor scores.
"""

import uuid
from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.hub_ops import events, procurement, purchasing, service
from gharbasket.modules.hub_ops.models import (
    GoodsReceipt,
    GoodsReceiptLine,
    InventoryLot,
    PurchaseOrder,
    PurchaseOrderLine,
    StockMove,
    Vendor,
)
from gharbasket.modules.hub_ops.schemas import (
    GoodsReceiptOut,
    LinePurchaseOrder,
    ReceiptIn,
    ReceiptLineOut,
    Ref,
    UserRef,
)
from gharbasket.modules.identity import api as identity_api
from gharbasket.platform import audit
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.locale import Language, localised
from gharbasket.platform.outbox import publish

RECEIVABLE = frozenset({"sent", "partly_received"})
BACKDATE = timedelta(hours=24)
CLOCK_SKEW = timedelta(minutes=1)  # a tablet's clock a little ahead is not "the future"
NO_SHELF_LIFE = timedelta(days=365)


def receipt_not_found() -> ProblemError:
    return ProblemError(404, "hub_ops.receipt_not_found", "No such goods receipt.")


def _invalid(reason: str, detail: str) -> ProblemError:
    return ProblemError(422, "hub_ops.receipt_invalid", detail, extra={"reason": reason})


def day_bounds(day: date) -> tuple[datetime, datetime]:
    """The IST day as [start, end) instants."""
    start = datetime.combine(day, time(), procurement.IST)
    return start, start + timedelta(days=1)


# --- the stock ledger ----------------------------------------------------------------------------


async def move(
    session: AsyncSession,
    *,
    hub_id: uuid.UUID,
    product_id: uuid.UUID,
    unit: str,
    kind: str,
    ref_id: uuid.UUID,
    qty: Decimal,
    lot: InventoryLot | None = None,
    reason: str | None = None,
    by_user_id: uuid.UUID | None = None,
    kept_item_id: uuid.UUID | None = None,
) -> bool:
    """Write one stock move and apply it to its (row-locked) lot. When the same kind, source row,
    product and lot already moved, nothing changes and it returns False: replays never move stock
    twice. `kept_item_id` names the kept item the move released or packed (§3.5)."""
    written = await session.execute(
        insert(StockMove)
        .values(
            hub_id=hub_id,
            lot_id=None if lot is None else lot.id,
            kept_item_id=kept_item_id,
            product_id=product_id,
            unit=unit,
            qty=qty,
            kind=kind,
            ref_id=ref_id,
            reason=reason,
            by_user_id=by_user_id,
        )
        .on_conflict_do_nothing()
        .returning(StockMove.id)
    )
    if written.first() is None:
        return False
    if lot is not None:
        lot.qty_on_hand += qty
        await session.flush()
    return True


# --- receiving -----------------------------------------------------------------------------------


async def _supplier(
    session: AsyncSession, hub_id: uuid.UUID, body: ReceiptIn
) -> tuple[uuid.UUID, PurchaseOrder | None]:
    """The vendor, and the PO row-locked after the hub and its plan (the procurement lock order)."""
    if body.purchase_order_id is None:
        assert body.vendor_id is not None  # the schema requires one
        vendor = await session.get(Vendor, body.vendor_id)
        if vendor is None or vendor.hub_id != hub_id:
            raise service.vendor_not_found()
        if not vendor.active:
            raise procurement.vendor_inactive()
        return vendor.id, None
    po = await purchasing.get_po(session, body.purchase_order_id)
    if po.hub_id != hub_id:
        raise purchasing.po_not_found()
    await procurement.get_plan(session, plan_id=po.plan_id, for_update=True)
    po = await purchasing.get_po(session, po.id, for_update=True)
    if po.status not in RECEIVABLE:
        raise ProblemError(
            409, "hub_ops.po_not_receivable", "Goods are received only against a sent order."
        )
    if body.vendor_id not in (None, po.vendor_id):
        raise _invalid("vendor_mismatch", "The vendor is not the purchase order's.")
    return po.vendor_id, po


def _received_at(value: datetime | None) -> datetime:
    now = datetime.now(UTC)
    if value is None:
        return now
    if not now - BACKDATE <= value <= now + CLOCK_SKEW:
        raise _invalid("received_at", "Goods are received now or in the last 24 hours.")
    return value.astimezone(UTC)


async def receive(
    session: AsyncSession, hub_id: uuid.UUID, body: ReceiptIn, actor_user_id: uuid.UUID
) -> GoodsReceipt:
    """Record a receipt (§3.4 rules): lines, a lot and a `receive` move per accepted line, the
    PO's received quantities and status, `hub_ops.goods_received` and an audit entry."""
    await service.get_hub(session, hub_id, for_update=True)
    received_at = _received_at(body.received_at)
    seen: set[uuid.UUID] = set()
    for line in body.lines:
        if line.product_id in seen:
            raise _invalid("duplicate_product", "Each product is on the receipt once.")
        seen.add(line.product_id)
    vendor_id, po = await _supplier(session, hub_id, body)
    products = await catalog_api.stock_products(session, list(seen))
    unstocked = sorted(
        str(p)
        for p in seen
        if p not in products
        or not products[p].active
        or products[p].kind not in procurement.STOCKED_KINDS
    )
    if unstocked:
        raise ProblemError(
            422,
            "hub_ops.product_not_stocked",
            "The hub does not stock these products.",
            extra={"product_ids": unstocked},
        )
    po_lines: dict[uuid.UUID, PurchaseOrderLine] = {}
    if po is not None:
        rows = await session.scalars(
            select(PurchaseOrderLine).where(PurchaseOrderLine.po_id == po.id).with_for_update()
        )
        po_lines = {x.product_id: x for x in rows}

    receipt = GoodsReceipt(
        hub_id=hub_id,
        vendor_id=vendor_id,
        purchase_order_id=None if po is None else po.id,
        received_at=received_at,
        received_by=actor_user_id,
        total_paise=0,
        unpriced_lines=0,
    )
    session.add(receipt)
    await session.flush()
    lines: list[tuple[GoodsReceiptLine, PurchaseOrderLine | None]] = []
    for x in body.lines:
        product = products[x.product_id]
        unit = procurement.OPS_UNIT[product.step_unit][0]
        received = procurement.check_qty(x.received_qty, unit)
        accepted = procurement.check_qty(x.accepted_qty, unit)
        if received == 0:
            raise ProblemError(422, "hub_ops.qty_invalid", "A received quantity is above 0.")
        if accepted > received:
            raise _invalid("accepted_over_received", "More was accepted than received.")
        if accepted < received and x.reject_reason is None:
            raise _invalid("reject_reason_missing", "Say why goods went back to the vendor.")
        # A shelf life of more than a year (salt, sugar) is capped, or the default would be refused.
        shelf = (
            NO_SHELF_LIFE
            if product.shelf_life_h is None
            else min(NO_SHELF_LIFE, timedelta(hours=product.shelf_life_h))
        )
        best_before = (x.best_before or received_at + shelf).astimezone(UTC)
        if not received_at < best_before <= received_at + NO_SHELF_LIFE:
            raise _invalid("best_before", "Best before is after receiving, within a year.")
        po_line = po_lines.get(x.product_id)
        cost = x.unit_cost_paise
        if "unit_cost_paise" not in x.model_fields_set and po_line is not None:
            cost = po_line.unit_cost_paise
        line = GoodsReceiptLine(
            receipt_id=receipt.id,
            product_id=product.id,
            unit=unit,
            received_qty=received,
            accepted_qty=accepted,
            grade=x.grade,
            reject_reason=x.reject_reason if accepted < received else None,
            unit_cost_paise=cost,
            amount_paise=procurement.amount(accepted, cost),
            best_before=best_before,
            ordered=po_line is not None,
            note=x.note,
        )
        session.add(line)
        lines.append((line, po_line))
    await session.flush()
    for line, po_line in lines:
        if po_line is not None:
            po_line.received_qty += line.received_qty
            po_line.accepted_qty += line.accepted_qty
        if line.accepted_qty == 0:
            continue
        lot = InventoryLot(
            hub_id=hub_id,
            product_id=line.product_id,
            unit=line.unit,
            source="receipt",
            receipt_line_id=line.id,
            grade=line.grade,
            qty_in=line.accepted_qty,
            qty_on_hand=Decimal(0),
            unit_cost_paise=line.unit_cost_paise,
            received_at=received_at,
            best_before=line.best_before,
        )
        session.add(lot)
        await session.flush()
        await move(
            session,
            hub_id=hub_id,
            product_id=line.product_id,
            unit=line.unit,
            kind="receive",
            ref_id=line.id,
            qty=line.accepted_qty,
            lot=lot,
            by_user_id=actor_user_id,
        )
    amounts = [line.amount_paise for line, _ in lines]
    receipt.total_paise = sum(a for a in amounts if a is not None)
    receipt.unpriced_lines = sum(a is None for a in amounts)
    if po is not None:
        done = all(x.received_qty >= x.qty for x in po_lines.values())
        po.status = "received" if done else "partly_received"
        po.closed_at = datetime.now(UTC) if done else None
        po.version += 1
    await session.flush()
    await session.refresh(receipt)
    await publish(
        session,
        events.GOODS_RECEIVED,
        {
            "receipt_id": str(receipt.id),
            "hub_id": str(hub_id),
            "vendor_id": str(vendor_id),
            "purchase_order_id": None if po is None else str(po.id),
            "lines_count": len(lines),
            "rejected_lines": sum(x.accepted_qty < x.received_qty for x, _ in lines),
            "total_paise": receipt.total_paise,
        },
    )
    await audit.record(
        session,
        actor_user_id=actor_user_id,
        action="goods_receipts.created",
        entity_type="goods_receipt",
        entity_id=str(receipt.id),
        after=body.model_dump(mode="json"),
    )
    return receipt


# --- reading -------------------------------------------------------------------------------------


async def get_receipt(session: AsyncSession, receipt_id: uuid.UUID) -> GoodsReceipt:
    receipt = await session.get(GoodsReceipt, receipt_id)
    if receipt is None:
        raise receipt_not_found()
    return receipt


async def hub_receipts(session: AsyncSession, hub_id: uuid.UUID, day: date) -> list[GoodsReceipt]:
    start, end = day_bounds(day)
    rows = await session.scalars(
        select(GoodsReceipt)
        .where(
            GoodsReceipt.hub_id == hub_id,
            GoodsReceipt.received_at >= start,
            GoodsReceipt.received_at < end,
        )
        .order_by(GoodsReceipt.received_at.desc(), GoodsReceipt.id.desc())
    )
    return list(rows)


async def receipts_out(
    session: AsyncSession, receipts: Iterable[GoodsReceipt], lang: Language
) -> list[GoodsReceiptOut]:
    receipts = list(receipts)
    lines = list(
        await session.scalars(
            select(GoodsReceiptLine).where(
                GoodsReceiptLine.receipt_id.in_([r.id for r in receipts])
            )
        )
    )
    lots = dict(
        (
            await session.execute(
                select(InventoryLot.receipt_line_id, InventoryLot.id).where(
                    InventoryLot.receipt_line_id.in_([x.id for x in lines])
                )
            )
        ).all()
    )
    products = await catalog_api.stock_products(session, list({x.product_id for x in lines}))
    vendors = dict(
        (
            await session.execute(
                select(Vendor.id, Vendor.name).where(Vendor.id.in_({r.vendor_id for r in receipts}))
            )
        ).all()
    )
    pos = {
        po.id: po
        for po in await session.scalars(
            select(PurchaseOrder).where(
                PurchaseOrder.id.in_({r.purchase_order_id for r in receipts})
            )
        )
    }
    users = await identity_api.user_contacts(session, [r.received_by for r in receipts])

    def line_out(x: GoodsReceiptLine) -> ReceiptLineOut:
        product = products[x.product_id]
        return ReceiptLineOut(
            **x.as_dict(),
            slug=product.slug,
            name=localised(product.names, lang),
            rejected_qty=float(x.received_qty - x.accepted_qty),
            lot_id=lots.get(x.id),
        )

    def out(r: GoodsReceipt) -> GoodsReceiptOut:
        po = pos.get(r.purchase_order_id) if r.purchase_order_id else None
        user = users.get(r.received_by)
        return GoodsReceiptOut(
            **r.as_dict(exclude=("received_by",)),
            vendor=Ref(id=r.vendor_id, name=vendors[r.vendor_id]),
            purchase_order=None
            if po is None
            else LinePurchaseOrder.model_validate(po, from_attributes=True),
            received_by=UserRef(user_id=r.received_by, name=None if user is None else user.name),
            lines=sorted(
                (line_out(x) for x in lines if x.receipt_id == r.id), key=lambda x: x.name
            ),
        )

    return [out(r) for r in receipts]
