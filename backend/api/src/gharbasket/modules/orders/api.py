"""Facade of M4 Orders & Checkout: the only module other modules may import (spec §3.1).

- M3 calls `create_from_cycle` on `subscriptions.box_approved` and `cancel_from_cycle` on a skip.
- The cut-off job and M6/M7 call `transition`; M6 packing calls `mark_shortage`;
  M6 procurement and M7 routing read `orders_for_delivery`.
"""

import uuid
from dataclasses import dataclass
from datetime import time

from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.orders import service
from gharbasket.modules.orders.models import Order, OrderLine
from gharbasket.modules.orders.service import (
    ApprovedCycle,
    CycleLine,
    OrderSummary,
    create_from_cycle,
    cutoff_for,
    orders_for_delivery,
)

__all__ = [
    "ApprovedCycle",
    "CycleLine",
    "Order",
    "OrderLine",
    "OrderSummary",
    "SlotRef",
    "cancel_from_cycle",
    "create_from_cycle",
    "cutoff_for",
    "get",
    "mark_shortage",
    "orders_for_delivery",
    "slot_ref",
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


async def slot_ref(session: AsyncSession, slot_id: uuid.UUID) -> SlotRef | None:
    """A slot by id (M3's delivery days point at M4's slots); None if unknown."""
    try:
        slot = await service.get_slot(session, slot_id)
    except Exception:
        return None
    return SlotRef(
        slot.id, slot.hub_id, slot.label, slot.kind, slot.start_time, slot.end_time, slot.active
    )
