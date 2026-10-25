"""What the cut-off did to a hub's box orders on a delivery day: the numbers behind M3's hub
cut-off monitor (M3 §3.4). A read of M4's own tables, nothing more."""

import uuid
from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.orders.models import STATUSES, Order, OrderEvent

# Every status the cut-off's payment has passed: locked, and whatever followed it.
PAID_BY_CUTOFF = tuple(s for s in STATUSES if s not in ("approved", "payment_failed", "cancelled"))


@dataclass(frozen=True)
class PaymentFailedOrder:
    order_id: uuid.UUID
    cycle_id: uuid.UUID | None
    household_id: uuid.UUID
    society_id: uuid.UUID
    total_paise: int


@dataclass(frozen=True)
class CutoffCounts:
    """Box orders of one hub and delivery day.

    `locked`: every order the cut-off or a later payment locked (and any status after locked).
    `payment_needed`: orders still `payment_failed`. `grace_paid`: orders that failed and then
    locked; only a top-up or `pay` inside the grace period can do that, so no clock is compared.
    `cancelled`: orders the grace job cancelled with reason `payment`.
    `settled_cycle_ids`: the cycles of all those orders plus the payment_failed ones, which M3
    leaves out of its own counts so a household is never counted on both sides."""

    locked: int
    payment_needed: int
    grace_paid: int
    cancelled: int
    settled_cycle_ids: frozenset[uuid.UUID]
    payment_failed: tuple[PaymentFailedOrder, ...]  # in no particular order


async def hub_cutoff_counts(
    session: AsyncSession, hub_id: uuid.UUID, delivery_date: date
) -> CutoffCounts:
    box = (Order.hub_id == hub_id, Order.delivery_date == delivery_date, Order.kind == "box")
    paid = (
        await session.execute(
            select(Order.id, Order.cycle_id).where(*box, Order.status.in_(PAID_BY_CUTOFF))
        )
    ).all()
    failed = [
        PaymentFailedOrder(o.id, o.cycle_id, o.household_id, o.society_id, o.total_paise)
        for o in (
            await session.execute(select(Order).where(*box, Order.status == "payment_failed"))
        ).scalars()
    ]
    grace_paid = (
        await session.execute(
            select(OrderEvent.order_id)
            .join(Order, Order.id == OrderEvent.order_id)
            .where(
                *box, OrderEvent.from_status == "payment_failed", OrderEvent.to_status == "locked"
            )
            .distinct()
        )
    ).all()
    cancelled = (
        await session.execute(
            select(Order.id, Order.cycle_id)
            .join(OrderEvent, OrderEvent.order_id == Order.id)
            .where(
                *box,
                Order.status == "cancelled",
                OrderEvent.to_status == "cancelled",
                OrderEvent.payload["reason"].as_string() == "payment",
            )
            .distinct()
        )
    ).all()
    settled = {cycle_id for _, cycle_id in (*paid, *cancelled) if cycle_id is not None}
    settled.update(o.cycle_id for o in failed if o.cycle_id is not None)
    return CutoffCounts(
        locked=len(paid),
        payment_needed=len(failed),
        grace_paid=len(grace_paid),
        cancelled=len(cancelled),
        settled_cycle_ids=frozenset(settled),
        payment_failed=tuple(failed),
    )
