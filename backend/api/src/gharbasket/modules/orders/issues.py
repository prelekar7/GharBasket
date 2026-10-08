"""Problems reported on an order (spec §3.2): the window, the claimed lines, the photos, the
auto-credit for small missing or damaged items, and the hub's triage of everything left open.

Money only moves through M5's refund facade, keyed `refund:<order>:issue_<hex>`, inside the
caller's transaction: an issue is credited and its refund posted together, or neither is. All the
credits on one order, auto or by the hub, never add up to more than was paid for it.
"""

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.orders import service
from gharbasket.modules.orders.events import ISSUE_REPORTED, ISSUE_RESOLVED
from gharbasket.modules.orders.models import (
    ISSUE_KINDS,
    Order,
    OrderEvent,
    OrderIssue,
    OrderIssueItem,
)
from gharbasket.platform import files
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.outbox import publish

WINDOW = timedelta(hours=48)  # from the `delivered` timeline row
MAX_ISSUES = 3
AUTO_CREDIT_CAP_PAISE = 5_000  # ₹50 per household per rolling 7 days, all or nothing
AUTO_CREDIT_PERIOD = timedelta(days=7)
PHOTO_RETENTION = timedelta(days=90)  # after the issue is resolved (M0 §3.1)
CALLBACK_MINUTES = 120
PHOTO_PURPOSE = "order_issue_photo"
LINE_KINDS = frozenset({"missing", "damaged", "quality", "wrong_item"})
AUTO_KINDS = frozenset({"missing", "damaged"})
AFTER_DISPATCH = frozenset({"dispatched", "at_society", "delivered", "undelivered", "redelivery"})


def _photo_invalid() -> ProblemError:
    return ProblemError(
        422, "orders.photo_key_invalid", "A photo was not uploaded by you for this report."
    )


async def _check_window(session: AsyncSession, order: Order, kind: str) -> None:
    reached = order.status == "delivered" if kind in LINE_KINDS else order.status in AFTER_DISPATCH
    delivered_at = (
        await session.execute(
            select(func.max(OrderEvent.at)).where(
                OrderEvent.order_id == order.id, OrderEvent.to_status == "delivered"
            )
        )
    ).scalar_one()
    if not reached or (delivered_at is not None and service.now() > delivered_at + WINDOW):
        raise ProblemError(
            409,
            "orders.issue_window_closed",
            "Problems can be reported from dispatch (late, other) or delivery (items) "
            "until 48 hours after delivery.",
        )


async def _priced(
    session: AsyncSession, order: Order, wanted: dict[uuid.UUID, int]
) -> list[OrderIssueItem]:
    """The claimed lines at the line's paid price; never more than was delivered, counting
    earlier claims on the same order."""
    lines = {line.product_id: line for line in await service.lines_of(session, order.id)}
    unknown = [str(pid) for pid in wanted if pid not in lines]
    if unknown:
        raise ProblemError(
            422,
            "orders.issue_item_unknown",
            "A product is not on this order.",
            extra={"product_ids": unknown},
        )
    rows = await session.execute(
        select(OrderIssueItem.product_id, func.sum(OrderIssueItem.qty))
        .join(OrderIssue, OrderIssue.id == OrderIssueItem.issue_id)
        .where(OrderIssue.order_id == order.id)
        .group_by(OrderIssueItem.product_id)
    )
    earlier = {pid: int(claimed) for pid, claimed in rows.all()}
    for pid, qty in wanted.items():
        room = lines[pid].qty - lines[pid].shortage_qty - earlier.get(pid, 0)
        if qty > room:
            raise ProblemError(
                422,
                "orders.issue_qty_invalid",
                "More than was delivered, counting earlier reports.",
                extra={"product_id": str(pid), "max_qty": max(0, room)},
            )
    return [
        OrderIssueItem(product_id=pid, qty=qty, unit_price_paise=lines[pid].unit_price_paise)
        for pid, qty in wanted.items()
    ]


async def _claim_photos(
    session: AsyncSession, keys: list[str], user_id: uuid.UUID, store: files.ObjectStore | None
) -> list[str]:
    """The caller's own uploaded issue photos, checked in storage and claimed for M4."""
    keys = list(dict.fromkeys(keys))
    if not keys:
        return []
    if store is None:
        raise files.storage_unavailable()
    used = await session.execute(select(OrderIssue.id).where(OrderIssue.photo_keys.overlap(keys)))
    if used.first() is not None:
        raise _photo_invalid()  # one photo backs one report
    for key in keys:
        upload = await files.verify(session, key, user_id=user_id, purpose=PHOTO_PURPOSE)
        stored = await store.head(key) if upload is not None else None
        if (
            upload is None
            or stored is None
            or (stored.content_type, stored.size_bytes) != (upload.content_type, upload.size_bytes)
        ):
            raise _photo_invalid()
    for key in keys:
        if (
            await files.claim(session, key, user_id=user_id, purpose=PHOTO_PURPOSE, by="orders")
            is None
        ):
            raise _photo_invalid()
    return keys


async def _refund(session: AsyncSession, order: Order, issue: OrderIssue, amount: int) -> uuid.UUID:
    owner = await identity_api.owner_of_household(session, order.household_id)
    if owner is None:
        raise ValueError(f"order {order.id} has no household owner")
    async with session.begin_nested():  # a refused refund leaves nothing behind
        entry = await billing_api.refund_to_wallet(
            session, owner, order.id, amount, reason=f"issue_{issue.id.hex}"
        )
    return entry.id


async def _close(
    session: AsyncSession,
    issue: OrderIssue,
    status: str,
    *,
    credit_paise: int,
    by: str,
    entry_id: uuid.UUID | None = None,
    resolver: uuid.UUID | None = None,
    note: str | None = None,
) -> None:
    issue.status, issue.open_reason, issue.credit_paise = status, None, credit_paise
    issue.resolved_by, issue.resolver_user_id, issue.resolution_note = by, resolver, note
    issue.resolved_at = at = service.now()
    issue.refund_entry_id = entry_id
    for key in issue.photo_keys:
        await files.set_delete_after(session, key, at + PHOTO_RETENTION)
    await session.flush()


async def _publish_resolved(session: AsyncSession, issue: OrderIssue) -> None:
    await publish(
        session,
        ISSUE_RESOLVED,
        {
            "issue_id": str(issue.id),
            "order_id": str(issue.order_id),
            "household_id": str(issue.household_id),
            "status": issue.status,
            "credit_paise": issue.credit_paise,
            "by": issue.resolved_by,
        },
    )


async def _creditable(session: AsyncSession, order: Order) -> int:
    """What may still be refunded on a row-locked order: its paid total minus the issue credits
    already made on it (M5 refunds nothing else per order yet, and billing.api has no per-order
    refund total)."""
    credited = (
        await session.execute(
            select(func.coalesce(func.sum(OrderIssue.credit_paise), 0)).where(
                OrderIssue.order_id == order.id, OrderIssue.status == "credited"
            )
        )
    ).scalar_one()
    return max(0, order.total_paise - int(credited))


async def _auto_credit(session: AsyncSession, order: Order, issue: OrderIssue) -> str | None:
    """Credit the issue at once if the rule allows it; else why it stays open."""
    if issue.kind == "damaged" and not issue.photo_keys:
        return "photo_needed"
    if issue.kind not in AUTO_KINDS or issue.amount_paise <= 0:
        return "triage"
    if issue.amount_paise > await _creditable(session, order):
        return "over_order_total"
    # One household's reports are counted one after the other, so two at once share the cap.
    lock_key = func.hashtextextended(f"orders.issue_credit:{issue.household_id}", 0)
    await session.execute(select(func.pg_advisory_xact_lock(lock_key)))
    used = (
        await session.execute(
            select(func.coalesce(func.sum(OrderIssue.credit_paise), 0)).where(
                OrderIssue.household_id == issue.household_id,
                OrderIssue.resolved_by == "auto",
                OrderIssue.resolved_at > service.now() - AUTO_CREDIT_PERIOD,
            )
        )
    ).scalar_one()
    if int(used) + issue.amount_paise > AUTO_CREDIT_CAP_PAISE:
        return "over_auto_credit_limit"
    try:
        entry_id = await _refund(session, order, issue, issue.amount_paise)
    except ProblemError as exc:
        if exc.code != "billing.wallet_limit":
            raise
        return "wallet_limit"
    await _close(
        session, issue, "credited", credit_paise=issue.amount_paise, by="auto", entry_id=entry_id
    )
    return None


async def report(
    session: AsyncSession,
    order: Order,
    *,
    kind: str,
    items: dict[uuid.UUID, int],
    note: str | None,
    photo_keys: list[str],
    user_id: uuid.UUID,
    store: files.ObjectStore | None,
) -> OrderIssue:
    """Record a problem on a row-locked order and credit it at once when the rule allows."""
    if kind not in ISSUE_KINDS:
        raise ProblemError(422, "orders.issue_kind_invalid", "Unknown kind of problem.")
    await _check_window(session, order, kind)
    count = (
        await session.execute(select(func.count()).where(OrderIssue.order_id == order.id))
    ).scalar_one()
    if count >= MAX_ISSUES:
        raise ProblemError(
            409, "orders.too_many_issues", f"At most {MAX_ISSUES} problems per order."
        )
    if kind in LINE_KINDS and not items:
        raise ProblemError(422, "orders.issue_items_required", "Say which items had the problem.")
    lines = await _priced(session, order, items) if items else []
    keys = await _claim_photos(session, photo_keys, user_id, store)
    issue = OrderIssue(
        order_id=order.id,
        household_id=order.household_id,
        hub_id=order.hub_id,
        kind=kind,
        status="open",
        open_reason="triage",
        note=note,
        photo_keys=keys,
        amount_paise=sum(line.qty * line.unit_price_paise for line in lines),
        credit_paise=0,
        created_by=user_id,
    )
    session.add(issue)
    await session.flush()
    for line in lines:
        line.issue_id = issue.id
    session.add_all(lines)
    await session.flush()
    issue.open_reason = await _auto_credit(session, order, issue)
    await session.flush()
    await publish(
        session,
        ISSUE_REPORTED,
        {
            "issue_id": str(issue.id),
            "order_id": str(order.id),
            "household_id": str(order.household_id),
            "hub_id": str(order.hub_id),
            "kind": kind,
            "amount_paise": issue.amount_paise,
            "status": issue.status,
        },
    )
    if issue.status != "open":
        await _publish_resolved(session, issue)
    await session.refresh(issue)
    return issue


async def resolve(
    session: AsyncSession,
    issue_id: uuid.UUID,
    outcome: str,
    credit_paise: int,
    actor: uuid.UUID,
    note: str | None,
) -> OrderIssue:
    """The hub's decision on an open issue: refund up to what the rule allows and what is left
    of the order's paid total, or reject."""
    order_id = (
        await session.execute(select(OrderIssue.order_id).where(OrderIssue.id == issue_id))
    ).scalar_one_or_none()
    if order_id is None:
        raise ProblemError(404, "orders.issue_not_found", "No such issue.")
    # The order first, as a report does, so credits on one order are counted one at a time.
    order = await service.get(session, order_id, for_update=True)
    issue = (
        await session.execute(select(OrderIssue).where(OrderIssue.id == issue_id).with_for_update())
    ).scalar_one()
    if issue.status != "open":
        raise ProblemError(409, "orders.issue_not_open", "This issue is already resolved.")
    if outcome == "reject":
        await _close(
            session, issue, "rejected", credit_paise=0, by="hub", resolver=actor, note=note
        )
        await _publish_resolved(session, issue)
        return issue
    if outcome != "credit":
        raise ValueError("outcome is 'credit' or 'reject'")
    most = min(
        issue.amount_paise if issue.kind in LINE_KINDS else order.total_paise,
        await _creditable(session, order),
    )
    if not 0 < credit_paise <= most:
        raise ProblemError(
            422,
            "orders.issue_credit_invalid",
            "The credit must be more than zero and at most what the issue allows.",
            extra={"max_paise": most},
        )
    entry_id = await _refund(session, order, issue, credit_paise)
    await _close(
        session,
        issue,
        "credited",
        credit_paise=credit_paise,
        by="hub",
        entry_id=entry_id,
        resolver=actor,
        note=note,
    )
    await _publish_resolved(session, issue)
    return issue


async def of_order(session: AsyncSession, order_id: uuid.UUID) -> list[OrderIssue]:
    rows = await session.execute(
        select(OrderIssue).where(OrderIssue.order_id == order_id).order_by(OrderIssue.id.desc())
    )
    return list(rows.scalars())


async def items_of(
    session: AsyncSession, issue_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[OrderIssueItem]]:
    found: dict[uuid.UUID, list[OrderIssueItem]] = {issue_id: [] for issue_id in issue_ids}
    rows = await session.execute(
        select(OrderIssueItem)
        .where(OrderIssueItem.issue_id.in_(issue_ids))
        .order_by(OrderIssueItem.product_id)
    )
    for item in rows.scalars():
        found[item.issue_id].append(item)
    return found


def summary(issue: OrderIssue) -> dict[str, Any]:
    """The issue fields of IssueOut other than its items."""
    resolution = None
    if issue.resolved_at is not None:
        resolution = {
            "by": issue.resolved_by,
            "note": issue.resolution_note,
            "at": issue.resolved_at,
        }
    return {
        "id": issue.id,
        "order_id": issue.order_id,
        "kind": issue.kind,
        "status": issue.status,
        "amount_paise": issue.amount_paise,
        "credit_paise": issue.credit_paise,
        "note": issue.note,
        "photo_count": len(issue.photo_keys),
        "resolution": resolution,
        "callback_within_minutes": CALLBACK_MINUTES if issue.status == "open" else None,
        "open_reason": issue.open_reason,
        "created_at": issue.created_at,
    }
