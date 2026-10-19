"""What M4's fulfilment events do to tiffin assignments (spec §3.4, §5): the rider's pickup, the
delivery and a failed delivery update the assignment's orders, and an assignment whose orders are
all delivered or failed becomes `completed` (`partners.assignment_completed`).

Consumers are idempotent: an order only moves forward (assigned, ready, picked up, then delivered
or failed), so a redelivered event changes nothing.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.partners import assignments, events
from gharbasket.modules.partners.models import LIVE, Assignment, AssignmentOrder
from gharbasket.platform.models import OutboxEvent
from gharbasket.platform.outbox import publish

# The order's status in the assignment after each event of its kind.
AFTER = {
    orders_api.DISPATCHED: "picked_up",
    orders_api.DELIVERED: "delivered",
    orders_api.DELIVERY_FAILED: "failed",
}
RANK = {"assigned": 0, "ready": 1, "picked_up": 2, "delivered": 3, "failed": 3}
DONE = ("delivered", "failed")


async def complete_if_done(session: AsyncSession, assignment: Assignment) -> bool:
    """`completed` once every order the assignment still holds is delivered or failed."""
    rows = list(
        (
            await session.execute(
                select(AssignmentOrder).where(
                    AssignmentOrder.assignment_id == assignment.id,
                    AssignmentOrder.status != "released",
                )
            )
        ).scalars()
    )
    if assignment.status not in LIVE or not rows or any(r.status not in DONE for r in rows):
        return False
    assignment.status = "completed"
    delivered = sum(r.portions for r in rows if r.status == "delivered")
    failed = sum(1 for r in rows if r.status == "failed")
    await assignments.event(session, assignment, "completed", None)
    await publish(
        session,
        events.ASSIGNMENT_COMPLETED,
        {
            "assignment_id": str(assignment.id),
            "partner_id": str(assignment.partner_id),
            "hub_id": str(assignment.hub_id),
            "date": assignment.on_date.isoformat(),
            "meal": assignment.meal,
            "portions_delivered": delivered,
            "orders_failed": failed,
        },
    )
    return True


async def on_order_event(session: AsyncSession, event: OutboxEvent) -> None:
    """`orders.dispatched`, `orders.delivered` and `orders.delivery_failed` of a tiffin."""
    payload = event.payload
    after = AFTER.get(event.event_type)
    if payload.get("kind") != "tiffin" or after is None:
        return
    row = (
        await session.execute(
            select(AssignmentOrder)
            .where(
                AssignmentOrder.order_id == uuid.UUID(str(payload["order_id"])),
                AssignmentOrder.status != "released",
            )
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None or RANK[after] <= RANK[row.status]:
        return  # not in an assignment (a hub's own move), or already there
    row.status = after
    assignment = await session.get(Assignment, row.assignment_id, with_for_update=True)
    if assignment is not None and not await complete_if_done(session, assignment):
        assignment.version += 1  # the kitchen's view shows the order's new status
        await session.flush()
