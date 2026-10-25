"""Fact rows of M4 for M12's analytics refresh (M12 §3.1): one row per order and per issue, with
ids, dates, statuses, counts and paise only (no name, phone or flat), read by delivery date.

Money follows `service.refunded_paise`: shortage refunds are already out of `total_paise` (which
follows the lines), `credited_paise` is the issue credits, and `refunded_paise` is everything else
paid back after packing (the move to `refunded` and the refunded carried items), so the two never
count the same rupee twice.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import BigInteger, Select, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.orders.models import (
    CarryOver,
    Order,
    OrderEvent,
    OrderIssue,
    OrderLine,
    Slot,
)
from gharbasket.modules.orders.service import IST


@dataclass(frozen=True)
class OrderFact:
    order_id: uuid.UUID
    household_id: uuid.UUID
    hub_id: uuid.UUID
    society_id: uuid.UUID
    kind: str  # box | tiffin | addon
    delivery_date: date
    slot_end: datetime  # the end of its slot on the delivery date (IST): on time if delivered by it
    status: str
    total_paise: int  # paid: the lines net of shortages, plus fees, less discounts
    fees_paise: int  # the pre-live delivery fee (M9 §3.6)
    refunded_paise: int  # the move to `refunded` and refunded carried items
    credited_paise: int  # credited issues
    lines: int
    short_lines: int  # lines short at packing
    delivered_at: datetime | None
    attempts: int  # trips out of the hub (moves to `dispatched`): 2 after a redelivery
    data_source: str


@dataclass(frozen=True)
class IssueFact:
    issue_id: uuid.UUID
    order_id: uuid.UUID
    hub_id: uuid.UUID
    kind: str
    status: str  # open | credited | rejected
    amount_paise: int  # claimed
    credit_paise: int
    resolved_by: str | None  # auto | hub
    created_at: datetime
    resolved_at: datetime | None
    data_source: str  # the order's


def _in_window(
    date_from: date, date_to: date, hub_ids: list[uuid.UUID] | None
) -> Select[uuid.UUID]:
    stmt = select(Order.id).where(Order.delivery_date >= date_from, Order.delivery_date <= date_to)
    return stmt if hub_ids is None else stmt.where(Order.hub_id.in_(hub_ids))


async def order_facts(
    session: AsyncSession,
    *,
    date_from: date,
    date_to: date,
    hub_ids: list[uuid.UUID] | None = None,
) -> list[OrderFact]:
    """Orders delivering between the two IST dates (inclusive), every kind and status."""
    ids = _in_window(date_from, date_to, hub_ids).subquery()
    lines = (
        select(
            OrderLine.order_id,
            func.count().label("lines"),
            func.count().filter(OrderLine.shortage_qty > 0).label("short"),
        )
        .where(OrderLine.order_id.in_(select(ids.c.id)))
        .group_by(OrderLine.order_id)
        .subquery()
    )
    credited = (
        select(OrderIssue.order_id, func.sum(OrderIssue.credit_paise).label("paise"))
        .where(OrderIssue.order_id.in_(select(ids.c.id)), OrderIssue.status == "credited")
        .group_by(OrderIssue.order_id)
        .subquery()
    )
    moves = (
        select(
            OrderEvent.order_id,
            func.sum(cast(OrderEvent.payload["refund_paise"].astext, BigInteger))
            .filter(OrderEvent.to_status == "refunded")
            .label("refunded"),
            func.max(OrderEvent.at).filter(OrderEvent.to_status == "delivered").label("delivered"),
            func.count().filter(OrderEvent.to_status == "dispatched").label("attempts"),
        )
        .where(OrderEvent.order_id.in_(select(ids.c.id)))
        .group_by(OrderEvent.order_id)
        .subquery()
    )
    carried = (
        select(CarryOver.from_order_id, func.sum(CarryOver.paid_paise).label("paise"))
        .where(CarryOver.from_order_id.in_(select(ids.c.id)), CarryOver.status == "refunded")
        .group_by(CarryOver.from_order_id)
        .subquery()
    )
    rows = await session.execute(
        select(
            Order,
            Slot.end_time,
            func.coalesce(lines.c.lines, 0),
            func.coalesce(lines.c.short, 0),
            func.coalesce(credited.c.paise, 0),
            func.coalesce(moves.c.refunded, 0) + func.coalesce(carried.c.paise, 0),
            moves.c.delivered,
            func.coalesce(moves.c.attempts, 0),
        )
        .join(Slot, Slot.id == Order.slot_id)
        .outerjoin(lines, lines.c.order_id == Order.id)
        .outerjoin(credited, credited.c.order_id == Order.id)
        .outerjoin(moves, moves.c.order_id == Order.id)
        .outerjoin(carried, carried.c.from_order_id == Order.id)
        .where(Order.id.in_(select(ids.c.id)))
        .order_by(Order.id)
    )
    return [
        OrderFact(
            order_id=order.id,
            household_id=order.household_id,
            hub_id=order.hub_id,
            society_id=order.society_id,
            kind=order.kind,
            delivery_date=order.delivery_date,
            slot_end=datetime.combine(order.delivery_date, end_time, IST),
            status=order.status,
            total_paise=order.total_paise,
            fees_paise=order.fees_paise,
            refunded_paise=int(refunded),
            credited_paise=int(credit),
            lines=int(n_lines),
            short_lines=int(n_short),
            delivered_at=delivered_at,
            attempts=int(attempts),
            data_source=order.data_source,
        )
        for order, end_time, n_lines, n_short, credit, refunded, delivered_at, attempts in rows
    ]


async def issue_facts(
    session: AsyncSession,
    *,
    date_from: date,
    date_to: date,
    hub_ids: list[uuid.UUID] | None = None,
) -> list[IssueFact]:
    """Issues on orders delivering between the two IST dates (inclusive), in id order."""
    ids = _in_window(date_from, date_to, hub_ids).subquery()
    rows = await session.execute(
        select(OrderIssue, Order.data_source)
        .join(Order, Order.id == OrderIssue.order_id)
        .where(OrderIssue.order_id.in_(select(ids.c.id)))
        .order_by(OrderIssue.id)
    )
    return [
        IssueFact(
            issue_id=issue.id,
            order_id=issue.order_id,
            hub_id=issue.hub_id,
            kind=issue.kind,
            status=issue.status,
            amount_paise=issue.amount_paise,
            credit_paise=issue.credit_paise,
            resolved_by=issue.resolved_by,
            created_at=issue.created_at,
            resolved_at=issue.resolved_at,
            data_source=data_source,
        )
        for issue, data_source in rows
    ]
