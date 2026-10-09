"""Facade of M4 Orders & Checkout: the only module other modules may import (spec §3.1, §3.2).

- M3 calls `create_from_cycle` on `subscriptions.box_approved` and `cancel_from_cycle` on a skip.
- The cut-off job and M6/M7/M8 call `transition`; M6 packing calls `mark_shortage`;
  M6 procurement and M7 routing read `orders_for_delivery`.
- The M12 console resolves open issues with `resolve_issue`.
"""

import uuid
from dataclasses import dataclass
from datetime import time
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.orders import issues, service
from gharbasket.modules.orders.events import CANCELLED, DELIVERED, LOCKED
from gharbasket.modules.orders.models import (
    Order,
    OrderEvent,
    OrderIssue,
    OrderIssueItem,
    OrderLine,
)
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
    "OrderIssue",
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
    "resolve_issue",
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
    actor_user_id: uuid.UUID | None = None,
    payload: dict[str, object] | None = None,
) -> Order:
    """Move an order along its kind's state machine (ADR-0026 §2, §3): `actor` is the kind of
    actor (`system`, `hub`, `packer`, `rider`, `captain`, `kitchen`, `customer`) and
    `actor_user_id` the person, recorded on the timeline row but never shown to customers.
    M4 checks only the state machine (409 `orders.illegal_transition`); the caller has already
    checked that this person may act on this order (packer of its hub, rider of its route,
    active captain of its society, kitchen of its assignment)."""
    order = await service.get(session, order_id, for_update=True)
    return await service.transition(
        session, order, to_status, actor=actor, actor_user_id=actor_user_id, payload=payload
    )


async def mark_shortage(
    session: AsyncSession,
    order_id: uuid.UUID,
    product_id: uuid.UUID,
    shortage_qty: int,
    substitute_product_id: uuid.UUID | None = None,
) -> OrderLine:
    """M6 packing (§3.1, ADR-0026 §6): refund the short steps to the wallet, or replace a
    whole short line with a same-or-cheaper substitute and refund the difference, in the
    caller's transaction; once per line. `billing.wallet_limit` is raised as is and then
    nothing is recorded."""
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


async def resolve_issue(
    session: AsyncSession,
    issue_id: uuid.UUID,
    outcome: Literal["credit", "reject"],
    credit_paise: int,
    actor: uuid.UUID,
    note: str | None = None,
) -> OrderIssue:
    """The M12 console's triage of an open issue (§3.2): refund `credit_paise` to the wallet
    (at most the issue's amount, or the order total for late/other, and never more than is left
    of what was paid for the order: `orders.issue_credit_invalid` carries `max_paise`) or reject
    it. The caller has checked `order_issues:resolve` for the issue's hub; `billing.wallet_limit`
    is raised as is."""
    return await issues.resolve(session, issue_id, outcome, credit_paise, actor, note)


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
        # Who made each move is the actor kind only: other people's user ids are not exported.
        found[event.order_id]["timeline"].append(
            event.as_dict(exclude={"order_id", "actor_user_id"})
        )
    return {
        "orders": [{**o.as_dict(exclude={"data_source"}), **found[o.id]} for o in orders],
        "issues": await _issues_for_export(session, ids),
    }


async def _issues_for_export(
    session: AsyncSession, order_ids: list[uuid.UUID]
) -> list[dict[str, Any]]:
    """Each issue on these orders with its items and `photo_count`: the photos themselves are
    never exported, nor which staff member resolved it."""
    found = list(
        (
            await session.execute(
                select(OrderIssue)
                .where(OrderIssue.order_id.in_(order_ids))
                .order_by(OrderIssue.created_at, OrderIssue.id)
            )
        ).scalars()
    )
    items: dict[uuid.UUID, list[dict[str, Any]]] = {issue.id: [] for issue in found}
    rows = await session.execute(
        select(OrderIssueItem).where(OrderIssueItem.issue_id.in_(list(items)))
    )
    for item in rows.scalars():
        items[item.issue_id].append(item.as_dict(exclude={"issue_id"}))
    return [
        {
            **issue.as_dict(exclude={"photo_keys", "resolver_user_id"}),
            "photo_count": len(issue.photo_keys),
            "items": items[issue.id],
        }
        for issue in found
    ]
