"""Facade of M4 Orders & Checkout: the only module other modules may import (spec §3.1).

- M3 calls `create_from_cycle` on `subscriptions.box_approved` and `cancel_from_cycle` on a skip.
- The cut-off job and M6/M7 call `transition`; M6 packing calls `mark_shortage`;
  M6 procurement and M7 routing read `orders_for_delivery`.
"""

import uuid
from dataclasses import dataclass
from datetime import date, time

from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.orders import service
from gharbasket.modules.orders.events import CANCELLED, DELIVERED, LOCKED
from gharbasket.modules.orders.models import STATUSES, Order, OrderLine
from gharbasket.modules.orders.service import (
    ApprovedCycle,
    CycleLine,
    OrderSummary,
    create_from_cycle,
    cutoff_for,
    orders_for_delivery,
)

__all__ = [
    "CANCELLED",
    "DELIVERED",
    "LOCKED",
    "NOT_SOLD",
    "SOLD",
    "ApprovedCycle",
    "CycleLine",
    "Order",
    "OrderLine",
    "OrderSummary",
    "SlotRef",
    "SoldLine",
    "cancel_from_cycle",
    "create_from_cycle",
    "cutoff_for",
    "get",
    "mark_shortage",
    "orders_for_delivery",
    "slot_ref",
    "sold_lines",
    "transition",
]


async def get(session: AsyncSession, order_id: uuid.UUID, *, for_update: bool = False) -> Order:
    return await service.get(session, order_id, for_update=for_update)


async def cancel_from_cycle(session: AsyncSession, cycle_id: uuid.UUID) -> Order | None:
    """A skipped cycle cancels its order if one exists and is not paid yet."""
    order = await service.by_cycle(session, cycle_id)
    if order is None:
        return None
    return await service.cancel(session, order, actor="system", reason="skipped")


async def transition(
    session: AsyncSession,
    order_id: uuid.UUID,
    to_status: str,
    *,
    actor: str,
    payload: dict[str, object] | None = None,
) -> Order:
    order = await service.get(session, order_id, for_update=True)
    return await service.transition(session, order, to_status, actor=actor, payload=payload)


async def mark_shortage(
    session: AsyncSession,
    order_id: uuid.UUID,
    product_id: uuid.UUID,
    shortage_qty: int,
    substitute_product_id: uuid.UUID | None = None,
) -> OrderLine:
    order = await service.get(session, order_id, for_update=True)
    return await service.mark_shortage(
        session, order, product_id, shortage_qty, substitute_product_id
    )


@dataclass(frozen=True)
class SlotRef:
    id: uuid.UUID
    hub_id: uuid.UUID
    label: str
    kind: str
    start: time
    end: time
    active: bool


# Demand that happened (M10 §3.6): every status that has passed `locked`, whether or not the
# delivery went well (undelivered, redelivery and refunded orders were still bought for). A new
# status in orders.models must be placed in one of the two (a test checks they cover STATUSES).
NOT_SOLD = ("approved", "payment_failed", "cancelled")
SOLD = tuple(s for s in STATUSES if s not in NOT_SOLD)


@dataclass(frozen=True)
class SoldLine:
    product_id: uuid.UUID
    order_kind: str  # the order's kind (box | addon | tiffin): an add-on inside a box is committed
    qty: int  # steps
    data_source: str  # real | synthetic


async def sold_lines(
    session: AsyncSession,
    hub_id: uuid.UUID,
    delivery_date: date,
    data_source: str | None = None,
) -> list[SoldLine]:
    """Every line of the hub's orders past `locked` on a delivery day (`SOLD`), with the order's
    kind, optionally of one data source (`real` | `synthetic`): what was really sold, for scoring
    forecasts (M10 `monitor_drift`), counted as the forecast job counts it."""
    summaries = await service.orders_for_delivery(session, hub_id, delivery_date, statuses=SOLD)
    return [
        SoldLine(line.product_id, summary.order.kind, line.qty, summary.order.data_source)
        for summary in summaries
        if data_source is None or summary.order.data_source == data_source
        for line in summary.lines
    ]


async def slot_ref(session: AsyncSession, slot_id: uuid.UUID) -> SlotRef | None:
    """A slot by id (M3's delivery days point at M4's slots); None if unknown."""
    try:
        slot = await service.get_slot(session, slot_id)
    except Exception:
        return None
    return SlotRef(
        slot.id, slot.hub_id, slot.label, slot.kind, slot.start_time, slot.end_time, slot.active
    )
