"""Purchase orders (spec §3.3): one draft per vendor from a plan, sent by the hub manager herself
(a WhatsApp link or a printout; nothing reaches a vendor automatically), or cancelled.

Costs are recorded for the hub's books and vendor scores only: no ledger entry, no payout
(vendors are paid outside the platform in the prototype, §11).
"""

import uuid
from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal
from urllib.parse import quote

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.hub_ops import events, procurement, service
from gharbasket.modules.hub_ops.models import (
    Hub,
    ProcurementPlan,
    PurchaseOrder,
    PurchaseOrderLine,
    Vendor,
)
from gharbasket.modules.hub_ops.schemas import (
    PurchaseOrderLineOut,
    PurchaseOrderOut,
    PurchaseOrderVendor,
)
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.locale import Language, localised
from gharbasket.platform.outbox import publish


def po_not_found() -> ProblemError:
    return ProblemError(404, "hub_ops.po_not_found", "No such purchase order.")


async def get_po(
    session: AsyncSession, po_id: uuid.UUID, *, for_update: bool = False
) -> PurchaseOrder:
    stmt = select(PurchaseOrder).where(PurchaseOrder.id == po_id)
    po = await session.scalar(
        stmt.with_for_update().execution_options(populate_existing=True) if for_update else stmt
    )
    if po is None:
        raise po_not_found()
    return po


async def _lines(
    session: AsyncSession, po_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[PurchaseOrderLine]]:
    found: dict[uuid.UUID, list[PurchaseOrderLine]] = defaultdict(list)
    rows = await session.execute(
        select(PurchaseOrderLine).where(PurchaseOrderLine.po_id.in_(po_ids))
    )
    for line in rows.scalars():
        found[line.po_id].append(line)
    return found


def _rupees(paise: int) -> str:
    return f"₹{Decimal(paise) / 100:,.2f}"


def _qty(qty: Decimal) -> str:
    return f"{qty.normalize():f}"


def share_text(
    hub: Hub, po: PurchaseOrder, lines: list[PurchaseOrderLine], names: dict[uuid.UUID, str]
) -> str:
    """The PO as plain text for WhatsApp or print (English in v1)."""
    day: date = po.delivery_date
    text = [
        f"GharBasket {hub.name} · PO {po.number or 'draft'}",
        f"Deliver by {hub.timings['receiving']} on {day:%a} {day.day} {day:%b %Y} to {hub.address}",
    ]
    for n, line in enumerate(lines, start=1):
        what = f"{n}. {names[line.product_id]}: {_qty(line.qty)} {line.unit}"
        if line.amount_paise is None or line.unit_cost_paise is None:
            text.append(f"{what} (no price agreed)")
        else:
            cost = _rupees(line.unit_cost_paise)
            text.append(f"{what} @ {cost}/{line.unit} = {_rupees(line.amount_paise)}")
    total = f"Total {_rupees(po.total_paise)}"
    if po.unpriced_lines:
        total += f" + {po.unpriced_lines} item{'s' * (po.unpriced_lines > 1)} at market price"
    return "\n".join([*text, total])


async def _english_names(
    session: AsyncSession, product_ids: list[uuid.UUID]
) -> dict[uuid.UUID, str]:
    products = await catalog_api.stock_products(session, product_ids)
    return {p.id: localised(p.names, "en") for p in products.values()}


async def generate(session: AsyncSession, plan: ProcurementPlan, actor_user_id: uuid.UUID) -> None:
    """Replace the row-locked plan's drafts with one draft per vendor for the lines to order."""
    if plan.status == "closed":
        raise procurement.plan_not_editable()
    on_po = await procurement.ordered(session, plan.id)
    lines = [
        line
        for line in await procurement.lines_of(session, plan.id)
        if line.order_qty > 0
        and not (line.product_id in on_po and on_po[line.product_id].status in procurement.SENT)
    ]
    if not lines:
        raise ProblemError(409, "hub_ops.nothing_to_order", "No line is left to order.")
    missing = sorted(str(line.product_id) for line in lines if line.vendor_id is None)
    if missing:
        raise ProblemError(
            422,
            "hub_ops.vendor_missing",
            "Choose a vendor for every line.",
            extra={"product_ids": missing},
        )
    await session.execute(
        delete(PurchaseOrder).where(
            PurchaseOrder.plan_id == plan.id, PurchaseOrder.status == "draft"
        )
    )
    hub = await service.get_hub(session, plan.hub_id)
    names = await _english_names(session, [line.product_id for line in lines])
    by_vendor: dict[uuid.UUID, list] = defaultdict(list)
    for line in sorted(lines, key=lambda x: names[x.product_id]):
        assert line.vendor_id is not None
        by_vendor[line.vendor_id].append(line)
    for vendor_id, vendor_lines in by_vendor.items():
        po = PurchaseOrder(
            plan_id=plan.id,
            hub_id=plan.hub_id,
            vendor_id=vendor_id,
            delivery_date=plan.delivery_date,
            status="draft",
            plan_version=plan.version,
            created_by=actor_user_id,
        )
        po_lines = [
            PurchaseOrderLine(
                product_id=line.product_id,
                unit=line.unit,
                qty=line.order_qty,
                unit_cost_paise=line.unit_cost_paise,
                amount_paise=procurement.amount(line.order_qty, line.unit_cost_paise),
                received_qty=Decimal(0),
                accepted_qty=Decimal(0),
            )
            for line in vendor_lines
        ]
        po.total_paise = sum(x.amount_paise or 0 for x in po_lines)
        po.unpriced_lines = sum(x.amount_paise is None for x in po_lines)
        po.share_text = share_text(hub, po, po_lines, names)
        session.add(po)
        await session.flush()
        await session.refresh(po)
        for x in po_lines:
            x.po_id = po.id
        session.add_all(po_lines)
    await session.flush()


async def plan_pos(session: AsyncSession, plan_id: uuid.UUID) -> list[PurchaseOrder]:
    rows = await session.execute(
        select(PurchaseOrder).where(
            PurchaseOrder.plan_id == plan_id, PurchaseOrder.status != "cancelled"
        )
    )
    return list(rows.scalars())


async def hub_pos(
    session: AsyncSession, hub_id: uuid.UUID, day: date, status: str | None
) -> list[PurchaseOrder]:
    stmt = select(PurchaseOrder).where(
        PurchaseOrder.hub_id == hub_id, PurchaseOrder.delivery_date == day
    )
    if status is not None:
        stmt = stmt.where(PurchaseOrder.status == status)
    return list((await session.scalars(stmt)).all())


async def send(session: AsyncSession, po: PurchaseOrder, actor_user_id: uuid.UUID) -> None:
    """draft -> sent with its number; the plan becomes `ordered`. The caller holds the hub, plan
    and PO locks (in that order), so numbers per hub and day never collide."""
    if po.status != "draft":
        raise ProblemError(409, "hub_ops.po_not_draft", "Only a draft can be sent.")
    plan = await procurement.get_plan(session, plan_id=po.plan_id)
    if po.plan_version != plan.version:
        raise ProblemError(409, "hub_ops.po_stale", "The plan changed: generate the orders again.")
    vendor = await session.get(Vendor, po.vendor_id)
    if vendor is None or not vendor.active:
        raise procurement.vendor_inactive()
    hub = await service.get_hub(session, po.hub_id)
    numbered = await session.scalar(
        select(func.count()).where(
            PurchaseOrder.hub_id == po.hub_id,
            PurchaseOrder.delivery_date == po.delivery_date,
            PurchaseOrder.number.is_not(None),
        )
    )
    po.number = f"{hub.slug.upper()}-{po.delivery_date:%y%m%d}-{(numbered or 0) + 1:02d}"
    po.status, po.sent_at, po.sent_by = "sent", datetime.now(UTC), actor_user_id
    po.version += 1
    lines = (await _lines(session, [po.id]))[po.id]
    po.share_text = share_text(
        hub, po, lines, await _english_names(session, [x.product_id for x in lines])
    )
    if plan.status == "draft":
        plan.status = "ordered"
    await session.flush()
    await publish(
        session,
        events.PURCHASE_ORDER_SENT,
        {
            "purchase_order_id": str(po.id),
            "number": po.number,
            "plan_id": str(po.plan_id),
            "hub_id": str(po.hub_id),
            "vendor_id": str(po.vendor_id),
            "delivery_date": po.delivery_date.isoformat(),
            "lines_count": len(lines),
            "total_paise": po.total_paise,
        },
    )


async def cancel(session: AsyncSession, po: PurchaseOrder, reason: str) -> None:
    """A draft, or a sent PO with nothing received, -> cancelled; a plan left with no sent PO
    returns to `draft`. The caller holds the plan and PO locks."""
    lines = (await _lines(session, [po.id]))[po.id]
    received = any(line.received_qty > 0 for line in lines)
    if po.status not in ("draft", "sent") or received:
        raise ProblemError(
            409, "hub_ops.po_not_cancellable", "This purchase order cannot be cancelled."
        )
    po.status, po.cancel_reason, po.closed_at = "cancelled", reason, datetime.now(UTC)
    po.version += 1
    await session.flush()
    plan = await procurement.get_plan(session, plan_id=po.plan_id)
    if plan.status == "ordered" and not any(
        x.status in procurement.SENT for x in await plan_pos(session, plan.id)
    ):
        plan.status = "draft"
    await session.flush()


async def pos_out(
    session: AsyncSession, pos: list[PurchaseOrder], lang: Language
) -> list[PurchaseOrderOut]:
    """The POs by vendor name, with their lines in the order they were written."""
    lines = await _lines(session, [po.id for po in pos])
    products = await catalog_api.stock_products(
        session, list({x.product_id for ls in lines.values() for x in ls})
    )
    vendors = {
        v.id: v
        for v in await session.scalars(
            select(Vendor).where(Vendor.id.in_({po.vendor_id for po in pos}))
        )
    }
    versions = dict(
        (
            await session.execute(
                select(ProcurementPlan.id, ProcurementPlan.version).where(
                    ProcurementPlan.id.in_({po.plan_id for po in pos})
                )
            )
        ).all()
    )

    def out(po: PurchaseOrder) -> PurchaseOrderOut:
        vendor = vendors[po.vendor_id]
        url = None
        if vendor.phone_e164 and vendor.whatsapp:
            url = f"https://wa.me/{vendor.phone_e164.lstrip('+')}?text={quote(po.share_text)}"
        names = {x.product_id: localised(products[x.product_id].names, lang) for x in lines[po.id]}
        return PurchaseOrderOut(
            **po.as_dict(),
            vendor=PurchaseOrderVendor(**vendor.as_dict(), phone=vendor.phone_e164),
            lines=[
                PurchaseOrderLineOut(
                    **x.as_dict(), slug=products[x.product_id].slug, name=names[x.product_id]
                )
                for x in sorted(lines[po.id], key=lambda x: names[x.product_id])
            ],
            whatsapp_url=url,
            stale=po.status == "draft" and po.plan_version != versions[po.plan_id],
        )

    return [
        out(po)
        for po in sorted(pos, key=lambda po: (vendors[po.vendor_id].name.lower(), po.created_at))
    ]
