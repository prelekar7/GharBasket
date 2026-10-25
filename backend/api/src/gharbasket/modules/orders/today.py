"""What M12's today board needs from M4 (M12 §3.3): the hub's open issues and the day's orders,
as counts. Ids, timestamps and numbers only; no household, name or flat."""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.orders.cutoff import PAID_BY_CUTOFF
from gharbasket.modules.orders.issues import CALLBACK_MINUTES
from gharbasket.modules.orders.models import Order, OrderIssue
from gharbasket.modules.orders.service import cutoff_for


@dataclass(frozen=True)
class OpenIssueSummary:
    count: int
    oldest_created_at: datetime | None  # None when nothing is open
    earliest_callback_due_at: datetime | None


@dataclass(frozen=True)
class TodayCounts:
    by_status: dict[str, int]  # every order of the day, all kinds; only statuses that occur
    boxes_total: int  # boxes not cancelled
    boxes_locked: int  # boxes the cut-off locked, and any status after locked
    boxes_by_society: dict[uuid.UUID, int]  # boxes not cancelled, per society
    cutoff_at: datetime  # 21:00 IST the day before, as a UTC instant


async def open_issue_summary(session: AsyncSession, *, hub_id: uuid.UUID) -> OpenIssueSummary:
    """The hub's open order issues: how many, the oldest `created_at`, and the earliest moment a
    callback is due (M4 §3.6's promise runs from `created_at`)."""
    count, oldest = (
        await session.execute(
            select(func.count(), func.min(OrderIssue.created_at)).where(
                OrderIssue.hub_id == hub_id, OrderIssue.status == "open"
            )
        )
    ).one()
    return OpenIssueSummary(
        count=int(count),
        oldest_created_at=oldest,
        earliest_callback_due_at=None
        if oldest is None
        else oldest + timedelta(minutes=CALLBACK_MINUTES),
    )


async def today_counts(session: AsyncSession, *, hub_id: uuid.UUID, on: date) -> TodayCounts:
    """The hub's orders delivering on the IST date `on` by status, and its boxes: the total,
    those the cut-off locked, and per society."""
    day = (Order.hub_id == hub_id, Order.delivery_date == on)
    by_status = {
        status: int(n)
        for status, n in (
            await session.execute(
                select(Order.status, func.count()).where(*day).group_by(Order.status)
            )
        ).all()
    }
    boxes = {
        society_id: int(n)
        for society_id, n in (
            await session.execute(
                select(Order.society_id, func.count())
                .where(*day, Order.kind == "box", Order.status != "cancelled")
                .group_by(Order.society_id)
            )
        ).all()
    }
    locked = await session.scalar(
        select(func.count()).where(*day, Order.kind == "box", Order.status.in_(PAID_BY_CUTOFF))
    )
    return TodayCounts(
        by_status=by_status,
        boxes_total=sum(boxes.values()),
        boxes_locked=int(locked or 0),
        boxes_by_society=boxes,
        cutoff_at=cutoff_for(on),
    )
