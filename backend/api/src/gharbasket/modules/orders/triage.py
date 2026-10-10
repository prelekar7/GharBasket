"""The console's queue of order issues (spec §3.6): which issues a hub sees, oldest first (the
callback promise), and the money facts beside each one that the hub needs to decide.

The decision itself goes through `issues.resolve` (orders.api.resolve_issue), which re-checks
everything under the order's row lock; the facts here are only what it would accept now.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.orders import issues, service
from gharbasket.modules.orders.models import Order, OrderIssue
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.pagination import decode_cursor, encode_cursor


def not_found() -> ProblemError:
    return ProblemError(404, "orders.issue_not_found", "No such issue.")


async def get(session: AsyncSession, issue_id: uuid.UUID) -> OrderIssue:
    issue = await session.get(OrderIssue, issue_id)
    if issue is None:
        raise not_found()
    return issue


async def queue(
    session: AsyncSession,
    *,
    hub_id: uuid.UUID | None,
    status: str,
    kind: str | None,
    cursor: str | None,
    limit: int,
) -> tuple[list[OrderIssue], str | None]:
    """Issues in this status (and kind) at this hub, or at every hub; oldest first, keyset paged
    on (created_at, id)."""
    stmt = select(OrderIssue).where(OrderIssue.status == status)
    if hub_id is not None:
        stmt = stmt.where(OrderIssue.hub_id == hub_id)
    if kind is not None:
        stmt = stmt.where(OrderIssue.kind == kind)
    if cursor is not None:
        after = decode_cursor(cursor).get("after")
        try:
            if not (isinstance(after, list) and len(after) == 2):
                raise ValueError
            last_at, last_id = datetime.fromisoformat(str(after[0])), uuid.UUID(str(after[1]))  # pyright: ignore[reportUnknownArgumentType]
        except ValueError as exc:
            raise ProblemError(422, "request.invalid_cursor", "The cursor is not valid.") from exc
        stmt = stmt.where(tuple_(OrderIssue.created_at, OrderIssue.id) > tuple_(last_at, last_id))
    stmt = stmt.order_by(OrderIssue.created_at, OrderIssue.id).limit(limit + 1)
    rows = list((await session.execute(stmt)).scalars())
    found = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        last = found[-1]
        next_cursor = encode_cursor({"after": [last.created_at.isoformat(), str(last.id)]})
    return found, next_cursor


@dataclass(frozen=True)
class Facts:
    society_id: uuid.UUID
    delivery_date: date
    order_total_paise: int
    creditable_paise: int
    max_credit_paise: int
    auto_credited_7d_paise: int
    callback_due_at: datetime | None


async def facts(session: AsyncSession, found: list[OrderIssue]) -> dict[uuid.UUID, Facts]:
    """Per issue: its order's total, what is left of it to refund, what resolve_issue would
    accept now (0 once resolved), and the household's automatic credits in the last 7 days."""
    order_ids = list({issue.order_id for issue in found})
    households = list({issue.household_id for issue in found})
    orders = {
        o.id: o
        for o in (await session.execute(select(Order).where(Order.id.in_(order_ids)))).scalars()
    }
    credited = dict(
        (
            await session.execute(
                select(OrderIssue.order_id, func.sum(OrderIssue.credit_paise))
                .where(OrderIssue.order_id.in_(order_ids), OrderIssue.status == "credited")
                .group_by(OrderIssue.order_id)
            )
        ).all()
    )
    auto = dict(
        (
            await session.execute(
                select(OrderIssue.household_id, func.sum(OrderIssue.credit_paise))
                .where(
                    OrderIssue.household_id.in_(households),
                    OrderIssue.resolved_by == "auto",
                    OrderIssue.resolved_at > service.now() - issues.AUTO_CREDIT_PERIOD,
                )
                .group_by(OrderIssue.household_id)
            )
        ).all()
    )
    out: dict[uuid.UUID, Facts] = {}
    for issue in found:
        order = orders[issue.order_id]
        creditable = max(0, order.total_paise - int(credited.get(order.id, 0)))
        is_open = issue.status == "open"
        allowed = issue.amount_paise if issue.kind in issues.LINE_KINDS else order.total_paise
        out[issue.id] = Facts(
            society_id=order.society_id,
            delivery_date=order.delivery_date,
            order_total_paise=order.total_paise,
            creditable_paise=creditable,
            max_credit_paise=min(allowed, creditable) if is_open else 0,
            auto_credited_7d_paise=int(auto.get(issue.household_id, 0)),
            callback_due_at=issue.created_at + timedelta(minutes=issues.CALLBACK_MINUTES)
            if is_open
            else None,
        )
    return out


async def hub_of(session: AsyncSession, issue_id: uuid.UUID) -> uuid.UUID:
    """The issue's hub, without loading the row (resolve locks and reads it afresh)."""
    hub_id = (
        await session.execute(select(OrderIssue.hub_id).where(OrderIssue.id == issue_id))
    ).scalar_one_or_none()
    if hub_id is None:
        raise not_found()
    return hub_id
