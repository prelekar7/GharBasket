"""Procurement plans (spec §3.3): what a hub buys for a delivery date, built at T-1 21:10 from the
cut-off's paid orders plus a buffer, rebuilt when the forecast or the payment grace changes it,
and edited by the hub manager line by line.

Orders come through `orders.api`, products through `catalog.api`; M6 never writes their tables.
"""

import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from typing import Any
from zoneinfo import ZoneInfo

from fastapi.encoders import jsonable_encoder
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.hub_ops import events, service
from gharbasket.modules.hub_ops.models import (
    ProcurementLine,
    ProcurementPlan,
    PurchaseOrder,
    PurchaseOrderLine,
    Vendor,
)
from gharbasket.modules.hub_ops.schemas import (
    LinePurchaseOrder,
    PlanTotals,
    ProcurementLineOut,
    ProcurementLinePatch,
    ProcurementPlanOut,
    Ref,
)
from gharbasket.modules.orders import api as orders_api
from gharbasket.platform import audit
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.locale import Language, localised
from gharbasket.platform.outbox import publish

IST = ZoneInfo("Asia/Kolkata")
STOCKED_KINDS = frozenset({"veg", "fruit", "staple", "salad"})  # tiffins and kits are not bought
ORDER_KINDS = ("box", "addon")
COMMITTED = ("locked", "picking", "packed")
AT_RISK = ("payment_failed",)
SENT = frozenset({"sent", "partly_received", "received"})  # a line on one of these is ordered
SHORT_LIFE_H, SHORT_LIFE_PCT = 48, 5
DAYS_BACK, DAYS_AHEAD = 7, 2
ZERO, MILLI = Decimal(0), Decimal("0.001")
MAX_QTY = Decimal("9999999.999")
# Step unit -> (operations unit, step units per operations unit), ADR-0026 §1.
OPS_UNIT = {
    "g": ("kg", 1000),
    "kg": ("kg", 1),
    "ml": ("l", 1000),
    "l": ("l", 1),
    "piece": ("piece", 1),
    "bunch": ("bunch", 1),
    "pack": ("pack", 1),
}


def _problem(status: int, code: str, detail: str, **extra: Any) -> ProblemError:
    return ProblemError(status, code, detail, extra=extra or None)


def plan_not_found() -> ProblemError:
    return _problem(404, "hub_ops.plan_not_found", "No procurement plan for this.")


def plan_not_editable() -> ProblemError:
    return _problem(409, "hub_ops.plan_not_editable", "This plan is closed.")


def vendor_inactive() -> ProblemError:
    return _problem(422, "hub_ops.vendor_inactive", "This vendor is not active.")


def plan_day(day: date | None, *, earliest: int = DAYS_BACK) -> date:
    """`day` (default today in IST) from today - `earliest` to today + 2, else 422."""
    today = datetime.now(IST).date()
    day = day or today
    if not today - timedelta(days=earliest) <= day <= today + timedelta(days=DAYS_AHEAD):
        raise _problem(422, "hub_ops.date_out_of_range", "That date is out of range.")
    return day


# --- quantities ----------------------------------------------------------------------------------


def to_ops(steps: Any, step_qty: int, step_unit: str) -> Decimal:
    """Steps (an int, or M10's float) in the operations unit, to 3 decimals."""
    per = OPS_UNIT[step_unit][1]
    return (Decimal(str(steps)) * step_qty / per).quantize(MILLI, ROUND_HALF_UP)


def round_up(qty: Decimal, unit: str) -> Decimal:
    """Up to 0.1 for kg and l, to a whole number otherwise."""
    grain = Decimal("0.1") if unit in ("kg", "l") else Decimal(1)
    return ((qty / grain).to_integral_value(ROUND_CEILING) * grain).quantize(MILLI)


def amount(qty: Decimal, unit_cost_paise: int | None) -> int | None:
    if unit_cost_paise is None:
        return None
    return int((qty * unit_cost_paise).quantize(Decimal(1), ROUND_HALF_UP))


def quantities(
    committed: Decimal,
    qtau: Decimal | None,
    buffer_pct: int,
    shelf_life_h: int | None,
    on_hand: Decimal,
    unit: str,
) -> tuple[str, Decimal, Decimal]:
    """(basis, buffer_qty, suggested_qty) of one line, by §3.3's rules."""
    basis, buffer = (
        ("buffer", committed * buffer_pct / 100) if qtau is None else ("forecast", max(ZERO, qtau))
    )
    if shelf_life_h is not None and shelf_life_h <= SHORT_LIFE_H:
        buffer = min(buffer, committed * min(buffer_pct, SHORT_LIFE_PCT) / 100)
    buffer = buffer.quantize(MILLI, ROUND_HALF_UP)
    return basis, buffer, round_up(max(ZERO, committed + buffer - on_hand), unit)


def check_qty(value: float, unit: str) -> Decimal:
    qty = Decimal(str(value))
    whole = unit not in ("kg", "l")
    if qty < 0 or qty > MAX_QTY or qty != qty.quantize(MILLI) or (whole and qty % 1):
        raise _problem(
            422,
            "hub_ops.qty_invalid",
            "Quantities are 0 or more with up to 3 decimals; whole for piece, bunch and pack.",
        )
    return qty


# --- what other sections fill in ------------------------------------------------------------------


async def _forecast_for(
    session: AsyncSession, hub_id: uuid.UUID, delivery_date: date
) -> dict[str, Any] | None:
    """M10's stored forecast for the hub and date, in the shape of `intel_gateway.api.
    hub_forecast` (`{"model", "items": [{"product_id", "uncommitted_q50", "uncommitted_qtau"}]}`,
    in steps), or None when there is none. It is wired to that facade when M10's forecast PRs
    (#118/#122) land; until then there is no forecast and every line uses the hub's buffer."""
    return None


async def _on_hand_for(
    session: AsyncSession, hub_id: uuid.UUID, delivery_date: date, product_ids: list[uuid.UUID]
) -> dict[uuid.UUID, Decimal]:
    """Usable stock for the date by product (grade A and B lots lasting past the day, §3.4). It
    reads the inventory lots that goods receipt brings; until they exist nothing is on hand."""
    return {}


async def _last_costs(
    session: AsyncSession, hub_id: uuid.UUID, product_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    """Each product's cost on its latest goods receipt line at the hub (§3.4). It reads the
    receipts that §3.4 brings; until they exist a new line has no cost."""
    return {}


# --- building ------------------------------------------------------------------------------------


async def _demand(
    session: AsyncSession, hub_id: uuid.UUID, day: date, statuses: tuple[str, ...]
) -> dict[uuid.UUID, Decimal]:
    """Steps not short of each line's current product (a substitute counts as itself)."""
    found: dict[uuid.UUID, Decimal] = defaultdict(Decimal)
    for order in await orders_api.orders_for_delivery(session, hub_id, day, statuses, ORDER_KINDS):
        for line in order.lines:
            steps = line.qty - (line.shortage_qty or 0)
            if steps > 0:
                found[line.product_id] += to_ops(steps, line.step_qty, line.step_unit)
    return found


async def ordered(session: AsyncSession, plan_id: uuid.UUID) -> dict[uuid.UUID, PurchaseOrder]:
    """The plan's purchase order (not cancelled) of each product on one."""
    rows = await session.execute(
        select(PurchaseOrderLine.product_id, PurchaseOrder)
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.po_id)
        .where(PurchaseOrder.plan_id == plan_id, PurchaseOrder.status != "cancelled")
    )
    return {product_id: po for product_id, po in rows.all()}


async def _default_vendors(
    session: AsyncSession, hub_id: uuid.UUID, product_ids: list[uuid.UUID]
) -> dict[uuid.UUID, uuid.UUID | None]:
    """The vendor of the product's last purchase order at the hub if still active, else the
    hub's default vendor."""
    rows = await session.execute(
        select(PurchaseOrderLine.product_id, Vendor.id, Vendor.active)
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderLine.po_id)
        .join(Vendor, Vendor.id == PurchaseOrder.vendor_id)
        .where(
            PurchaseOrder.hub_id == hub_id,
            PurchaseOrder.status != "cancelled",
            PurchaseOrderLine.product_id.in_(product_ids),
        )
        .order_by(PurchaseOrder.created_at.desc(), PurchaseOrder.id.desc())
    )
    last: dict[uuid.UUID, uuid.UUID | None] = {}
    for product_id, vendor_id, active in rows.all():
        last.setdefault(product_id, vendor_id if active else None)
    default = await session.scalar(
        select(Vendor.id).where(Vendor.hub_id == hub_id, Vendor.is_default)
    )
    return {p: last.get(p) or default for p in product_ids}


async def lines_of(session: AsyncSession, plan_id: uuid.UUID) -> list[ProcurementLine]:
    rows = await session.execute(select(ProcurementLine).where(ProcurementLine.plan_id == plan_id))
    return list(rows.scalars())


def _values(line: ProcurementLine) -> tuple[Any, ...]:
    return (
        line.committed_qty,
        line.at_risk_qty,
        line.forecast_q50,
        line.forecast_qtau,
        line.basis,
        line.buffer_qty,
        line.on_hand_qty,
        line.suggested_qty,
        line.order_qty,
        line.shelf_life_h,
    )


def totals(lines: list[ProcurementLine]) -> PlanTotals:
    amounts = [amount(line.order_qty, line.unit_cost_paise) for line in lines]
    return PlanTotals(
        lines=len(lines),
        order_paise=sum(a for a in amounts if a is not None),
        unpriced_lines=sum(a is None for a in amounts),
    )


async def build(
    session: AsyncSession, hub_id: uuid.UUID, day: date, *, existing_only: bool = False
) -> tuple[ProcurementPlan | None, str]:
    """Build the hub's plan for `day`, or rebuild it (§3.3). Returns the plan (None when
    `existing_only` and there is none) and what happened: built, rebuilt, unchanged, closed."""
    hub = await service.get_hub(session, hub_id, for_update=True)  # one build per hub at a time
    plan = await session.scalar(
        select(ProcurementPlan)
        .where(ProcurementPlan.hub_id == hub.id, ProcurementPlan.delivery_date == day)
        .with_for_update()
    )
    if plan is None and existing_only:
        return None, "none"
    if plan is not None and plan.status == "closed":
        return plan, "closed"
    committed = await _demand(session, hub.id, day, COMMITTED)
    at_risk = await _demand(session, hub.id, day, AT_RISK)
    forecast = await _forecast_for(session, hub.id, day)
    items = {uuid.UUID(str(i["product_id"])): i for i in (forecast or {}).get("items", [])}
    wanted = {*committed, *at_risk}
    wanted |= {p for p, i in items.items() if i["uncommitted_q50"] > 0 or i["uncommitted_qtau"] > 0}
    lines = (
        {} if plan is None else {line.product_id: line for line in await lines_of(session, plan.id)}
    )
    products = await catalog_api.stock_products(session, list(wanted | set(lines)))
    wanted = {p for p in wanted if p in products and products[p].kind in STOCKED_KINDS}
    on_hand = await _on_hand_for(session, hub.id, day, list(wanted | set(lines)))
    sent = (
        set()
        if plan is None
        else {p for p, po in (await ordered(session, plan.id)).items() if po.status in SENT}
    )
    new = [p for p in wanted if p not in lines]
    vendors = await _default_vendors(session, hub.id, new) if new else {}
    costs = await _last_costs(session, hub.id, new) if new else {}

    now = datetime.now(UTC)
    status = "built" if plan is None else "rebuilt"
    if plan is None:
        plan = ProcurementPlan(hub_id=hub.id, delivery_date=day, built_at=now)
        session.add(plan)
    before = (plan.source, plan.model, plan.buffer_pct, {p: _values(x) for p, x in lines.items()})
    plan.source = "fallback" if forecast is None else "ml"
    plan.model = None if forecast is None else forecast.get("model")
    plan.buffer_pct = hub.procurement_buffer_pct
    await session.flush()
    for product_id in sorted(wanted | set(lines)):
        line = lines.get(product_id)
        if product_id not in wanted and not (line and (line.edited or product_id in sent)):
            await session.delete(lines.pop(product_id))
            continue
        product = products[product_id]
        item = items.get(product_id)
        step = (product.step_qty, product.step_unit)
        if line is None:
            line = ProcurementLine(
                plan_id=plan.id,
                product_id=product_id,
                unit=OPS_UNIT[product.step_unit][0],
                edited=False,
                vendor_id=vendors.get(product_id),
                unit_cost_paise=costs.get(product_id),
            )
            session.add(line)
            lines[product_id] = line
        line.shelf_life_h = product.shelf_life_h
        line.committed_qty = committed.get(product_id, ZERO)
        line.at_risk_qty = at_risk.get(product_id, ZERO)
        line.forecast_q50 = None if item is None else to_ops(item["uncommitted_q50"], *step)
        line.forecast_qtau = None if item is None else to_ops(item["uncommitted_qtau"], *step)
        line.basis, line.buffer_qty, line.suggested_qty = quantities(
            line.committed_qty,
            line.forecast_qtau,
            plan.buffer_pct,
            product.shelf_life_h,
            on_hand.get(product_id, ZERO),
            line.unit,
        )
        line.on_hand_qty = on_hand.get(product_id, ZERO)
        if not line.edited and product_id not in sent:
            line.order_qty = line.suggested_qty
    after = (plan.source, plan.model, plan.buffer_pct, {p: _values(x) for p, x in lines.items()})
    if status == "rebuilt" and before == after:
        return plan, "unchanged"
    if status == "rebuilt":
        plan.version += 1
        plan.refreshed_at = now
    await session.flush()
    await session.refresh(plan)
    summary = totals(list(lines.values()))
    await publish(
        session,
        events.PROCUREMENT_PLAN_READY,
        {
            "plan_id": str(plan.id),
            "hub_id": str(hub.id),
            "delivery_date": day.isoformat(),
            "source": plan.source,
            "model": plan.model,
            "lines_count": summary.lines,
            "order_paise": summary.order_paise,
            "unpriced_lines": summary.unpriced_lines,
            "version": plan.version,
        },
    )
    return plan, status


# --- reading and editing --------------------------------------------------------------------------


async def get_plan(
    session: AsyncSession,
    *,
    plan_id: uuid.UUID | None = None,
    hub_id: uuid.UUID | None = None,
    day: date | None = None,
    for_update: bool = False,
) -> ProcurementPlan:
    stmt = select(ProcurementPlan)
    if plan_id is not None:
        stmt = stmt.where(ProcurementPlan.id == plan_id)
    else:
        stmt = stmt.where(ProcurementPlan.hub_id == hub_id, ProcurementPlan.delivery_date == day)
    plan = await session.scalar(
        stmt.with_for_update().execution_options(populate_existing=True) if for_update else stmt
    )
    if plan is None:
        raise plan_not_found()
    return plan


async def plan_out(
    session: AsyncSession, plan: ProcurementPlan, lang: Language
) -> ProcurementPlanOut:
    lines = await lines_of(session, plan.id)
    products = await catalog_api.stock_products(session, [line.product_id for line in lines])
    vendor_ids = {line.vendor_id for line in lines if line.vendor_id is not None}
    vendors = dict(
        (
            await session.execute(select(Vendor.id, Vendor.name).where(Vendor.id.in_(vendor_ids)))
        ).all()
    )
    on_po = await ordered(session, plan.id)

    def out(line: ProcurementLine) -> ProcurementLineOut:
        product, po = products[line.product_id], on_po.get(line.product_id)
        return ProcurementLineOut(
            **line.as_dict(),
            slug=product.slug,
            name=localised(product.names, lang),
            vendor=None
            if line.vendor_id is None
            else Ref(id=line.vendor_id, name=vendors[line.vendor_id]),
            amount_paise=amount(line.order_qty, line.unit_cost_paise),
            purchase_order=None
            if po is None
            else LinePurchaseOrder.model_validate(po, from_attributes=True),
        )

    items = sorted(
        (out(line) for line in lines), key=lambda x: (products[x.product_id].shelf, x.name)
    )
    return ProcurementPlanOut(**plan.as_dict(), totals=totals(lines), lines=items)


async def edit_line(
    session: AsyncSession,
    plan: ProcurementPlan,
    product_id: uuid.UUID,
    body: ProcurementLinePatch,
    *,
    expected_version: int,
    actor_user_id: uuid.UUID,
) -> None:
    """Change one line of a row-locked plan (§3.3 Editing); audited, version + 1 if anything
    changed."""
    service.check_version(plan.version, expected_version)
    if plan.status == "closed":
        raise plan_not_editable()
    line = await session.get(ProcurementLine, (plan.id, product_id), with_for_update=True)
    if line is None:
        raise _problem(404, "hub_ops.line_not_found", "This product is not on the plan.")
    po = (await ordered(session, plan.id)).get(product_id)
    if po is not None and po.status in SENT:
        raise _problem(409, "hub_ops.line_ordered", "This line is on a sent purchase order.")
    sent = body.model_fields_set
    changes: dict[str, Any] = {}
    if "order_qty" in sent:
        if body.order_qty is None:
            if line.edited:
                changes.update(order_qty=line.suggested_qty, edited=False, edit_reason=None)
        else:
            qty = check_qty(body.order_qty, line.unit)
            if (qty, True) != (line.order_qty, line.edited):
                if body.reason is None:
                    raise _problem(422, "hub_ops.reason_required", "Say why the quantity changes.")
                changes.update(order_qty=qty, edited=True, edit_reason=body.reason)
    if "vendor_id" in sent and body.vendor_id != line.vendor_id:
        if body.vendor_id is not None:
            vendor = await session.get(Vendor, body.vendor_id)
            if vendor is None or vendor.hub_id != plan.hub_id:
                raise service.vendor_not_found()
            if not vendor.active:
                raise vendor_inactive()
        changes["vendor_id"] = body.vendor_id
    for name in ("unit_cost_paise", "note"):
        if name in sent and getattr(body, name) != getattr(line, name):
            changes[name] = getattr(body, name)
    if not changes:
        return
    before = jsonable_encoder({name: getattr(line, name) for name in changes})
    for name, value in changes.items():
        setattr(line, name, value)
    plan.version += 1
    await audit.record(
        session,
        actor_user_id=actor_user_id,
        action="procurement.line_updated",
        entity_type="procurement_line",
        entity_id=f"{plan.id}:{product_id}",
        before=before,
        after=jsonable_encoder({**changes, "reason": body.reason}),
    )
    await session.flush()
