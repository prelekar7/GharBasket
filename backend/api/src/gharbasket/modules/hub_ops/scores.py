"""Weekly vendor scores (spec §3.4): how fresh, well priced, reliable and clean each vendor's goods
were over an IST week (Monday to Sunday), from its goods receipts and sent purchase orders.

Components are 0 … 1, or null without data; the score is their weighted mean over the components
that have data (freshness 40 %, price, reliability and 1 - defects 20 % each), to 2 decimals.
"""

import uuid
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from statistics import fmean, median

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import distinct_on, insert
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.hub_ops.models import (
    GoodsReceipt,
    GoodsReceiptLine,
    Hub,
    PurchaseOrder,
    PurchaseOrderLine,
    VendorScore,
)
from gharbasket.modules.hub_ops.procurement import IST

WEIGHTS = {"freshness": 40, "price": 20, "reliability": 20, "defects": 20}


def _mean(values: list[float]) -> float | None:
    return fmean(values) if values else None


def _stored(value: float | None, places: str = "0.001") -> Decimal | None:
    return None if value is None else Decimal(str(value)).quantize(Decimal(places), ROUND_HALF_UP)


async def compute(session: AsyncSession, week: date) -> int:
    """Score every vendor with a sent purchase order delivered in the week, or a receipt in it;
    writes (or rewrites) their rows for the week. Returns how many vendors were scored."""
    start = datetime.combine(week, time(), IST)
    end = start + timedelta(days=7)
    receipt_rows = (
        await session.execute(
            select(GoodsReceipt.hub_id, GoodsReceipt.vendor_id, GoodsReceiptLine)
            .join(GoodsReceipt, GoodsReceipt.id == GoodsReceiptLine.receipt_id)
            .where(GoodsReceipt.received_at >= start, GoodsReceipt.received_at < end)
        )
    ).all()
    # A PO cancelled by hand after sending is left out; one cancelled as not delivered counts 0.
    pos = list(
        await session.scalars(
            select(PurchaseOrder).where(
                PurchaseOrder.delivery_date.between(week, week + timedelta(days=6)),
                PurchaseOrder.sent_at.is_not(None),
                or_(
                    PurchaseOrder.status != "cancelled",
                    PurchaseOrder.cancel_reason == "not_delivered",
                ),
            )
        )
    )
    po_lines: dict[uuid.UUID, list[PurchaseOrderLine]] = defaultdict(list)
    for line in await session.scalars(
        select(PurchaseOrderLine).where(PurchaseOrderLine.po_id.in_([po.id for po in pos]))
    ):
        po_lines[line.po_id].append(line)
    first_receipts = dict(
        (
            await session.execute(
                select(GoodsReceipt.purchase_order_id, func.min(GoodsReceipt.received_at))
                .where(GoodsReceipt.purchase_order_id.in_([po.id for po in pos]))
                .group_by(GoodsReceipt.purchase_order_id)
            )
        ).all()
    )
    packing = dict((await session.execute(select(Hub.id, Hub.timings["packing"].astext))).all())

    costs: dict[tuple[uuid.UUID, uuid.UUID], list[tuple[uuid.UUID, int]]] = defaultdict(list)
    lines_of: dict[uuid.UUID, list[tuple[uuid.UUID, GoodsReceiptLine]]] = defaultdict(list)
    for hub_id, vendor_id, line in receipt_rows:
        lines_of[vendor_id].append((hub_id, line))
        if line.unit_cost_paise is not None:
            costs[hub_id, line.product_id].append((vendor_id, line.unit_cost_paise))
    pos_of: dict[uuid.UUID, list[PurchaseOrder]] = defaultdict(list)
    for po in pos:
        pos_of[po.vendor_id].append(po)

    def price(hub_id: uuid.UUID, line: GoodsReceiptLine) -> float:
        found = costs[hub_id, line.product_id]
        if len({vendor for vendor, _ in found}) == 1 or not line.unit_cost_paise:
            return 1.0  # a lone vendor (or a free line) is the market
        return min(1.0, median(cost for _, cost in found) / line.unit_cost_paise)

    def on_time(po: PurchaseOrder) -> bool:
        due = datetime.combine(po.delivery_date, time.fromisoformat(packing[po.hub_id]), IST)
        first = first_receipts.get(po.id)
        return first is not None and first <= due

    rows = []
    for vendor_id in {*lines_of, *pos_of}:
        lines = lines_of.get(vendor_id, [])
        vendor_pos = pos_of.get(vendor_id, [])
        accepted = [x for _, x in lines if x.accepted_qty > 0]
        fills = [
            0.0 if po.status == "cancelled" else min(1.0, float(x.received_qty / x.qty))
            for po in vendor_pos
            for x in po_lines[po.id]
        ]
        fill, timely = _mean(fills), _mean([float(on_time(po)) for po in vendor_pos])
        parts = {
            "freshness": _mean([float(x.grade == "A") for x in accepted]),
            "price": _mean([price(h, x) for h, x in lines if x.unit_cost_paise is not None]),
            "reliability": None if fill is None or timely is None else (fill + timely) / 2,
            "defects": _mean([float(1 - x.accepted_qty / x.received_qty) for _, x in lines]),
        }
        weighted = {
            name: (1 - value if name == "defects" else value)
            for name, value in parts.items()
            if value is not None
        }
        score = sum(WEIGHTS[n] * v for n, v in weighted.items()) / sum(WEIGHTS[n] for n in weighted)
        rows.append(
            {
                "vendor_id": vendor_id,
                "week": week,
                **{name: _stored(value) for name, value in parts.items()},
                "score": _stored(score, "0.01"),
                "pos_count": len(vendor_pos),
                "receipt_lines_count": len(lines),
            }
        )
    if rows:
        stmt = insert(VendorScore).values(rows)
        await session.execute(
            stmt.on_conflict_do_update(
                index_elements=[VendorScore.vendor_id, VendorScore.week],
                set_={
                    name: stmt.excluded[name]
                    for name in (*WEIGHTS, "score", "pos_count", "receipt_lines_count")
                }
                | {"computed_at": func.now()},
            )
        )
    return len(rows)


async def latest(session: AsyncSession, vendor_ids: list[uuid.UUID]) -> dict[uuid.UUID, float]:
    """Each vendor's score of its latest scored week."""
    rows = await session.execute(
        select(VendorScore.vendor_id, VendorScore.score)
        .where(VendorScore.vendor_id.in_(vendor_ids))
        .order_by(VendorScore.vendor_id, VendorScore.week.desc())
        .ext(distinct_on(VendorScore.vendor_id))
    )
    return {vendor_id: float(score) for vendor_id, score in rows.all()}
