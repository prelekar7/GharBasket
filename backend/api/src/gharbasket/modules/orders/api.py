"""Facade of M4 Orders & Checkout: the only module other modules may import (spec §3.1).

- M3 calls `create_from_cycle` on `subscriptions.box_approved` and `cancel_from_cycle` on a skip.
- The cut-off job and M6/M7 call `transition`; M6 packing calls `mark_shortage`;
  M6 procurement and M7 routing read `orders_for_delivery`.
"""

import uuid
from dataclasses import dataclass
from datetime import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.orders import service
from gharbasket.modules.orders.events import CANCELLED, DELIVERED, LOCKED
from gharbasket.modules.orders.models import Order, OrderEvent, OrderLine
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
    "ApprovedCycle",
    "CycleLine",
    "Order",
    "OrderLine",
    "OrderSummary",
    "SlotRef",
    "cancel_from_cycle",
    "create_from_cycle",
    "cutoff_for",
    "export_for_user",
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


async def export_for_user(session: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
    """M4's section of the user's data export (M1 §3.3), read-only: orders with lines and
    timelines, and issues (their photos counted, never included)."""
    household = await identity_api.household_for_user(session, user_id)
    if household is None:
        return {"orders": [], "issues": []}
    orders = list(
        (
            await session.execute(
                select(Order)
                .where(Order.household_id == household.id)
                .order_by(Order.delivery_date, Order.id)
            )
        ).scalars()
    )
    ids = [order.id for order in orders]
    found: dict[uuid.UUID, dict[str, list[dict[str, Any]]]] = {
        i: {"lines": [], "timeline": []} for i in ids
    }
    lines = await session.execute(select(OrderLine).where(OrderLine.order_id.in_(ids)))
    for line in lines.scalars():
        found[line.order_id]["lines"].append(line.as_dict(exclude={"order_id"}))
    events = await session.execute(
        select(OrderEvent).where(OrderEvent.order_id.in_(ids)).order_by(OrderEvent.id)
    )
    for event in events.scalars():
        found[event.order_id]["timeline"].append(event.as_dict(exclude={"order_id"}))
    return {
        "orders": [{**o.as_dict(exclude={"data_source"}), **found[o.id]} for o in orders],
        "issues": await _issues_for_export(session, ids),
    }


async def _issues_for_export(session: AsyncSession, order_ids: list[uuid.UUID]) -> list[Any]:
    # TODO(M4 order issues): the issues table arrives with feat/M4-order-issues. Then list each
    # issue of these orders with `photo_count` (the photos themselves are never exported).
    return []
