"""Facade of M4 Orders & Checkout: the only module other modules may import (spec §3.1, §3.2).

- M3 calls `create_from_cycle` on `subscriptions.box_approved` and `cancel_from_cycle` on a skip,
  and shows a `payment_failed` week's amount due until `grace_until_for` its delivery date.
- The cut-off job and M6/M7/M8 call `transition`; M6 packing calls `mark_shortage`;
  M6 procurement and M7 routing read `orders_for_delivery`.
- The M12 console resolves open issues with `resolve_issue`.
- M3 shows a box's pre-live delivery fee before approval with `box_fee` (M9 §3.6).
- M12 pulls `order_facts` and `issue_facts` for its analytics refresh, from
  `first_delivery_date` on for a rebuild (M12 §3.1).
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Any, Literal

from sqlalchemy import exists, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.orders import carry, issues, service
from gharbasket.modules.orders.carry import CarriedLine
from gharbasket.modules.orders.cutoff import CutoffCounts, PaymentFailedOrder, hub_cutoff_counts
from gharbasket.modules.orders.events import (
    CANCELLED,
    CARRY_OVER_ATTACHED,
    CARRY_OVER_EXPIRED,
    DELIVERED,
    DELIVERY_FAILED,
    DISPATCHED,
    LOCKED,
    PACKED,
    PAYMENT_FAILED,
    TIFFIN_BOOKED,
)
from gharbasket.modules.orders.facts import (
    IssueFact,
    OrderFact,
    first_delivery_date,
    issue_facts,
    order_facts,
)
from gharbasket.modules.orders.fees import box_fee
from gharbasket.modules.orders.models import (
    STATUSES,
    CarryOver,
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
    grace_until_for,
    orders_for_delivery,
)
from gharbasket.modules.orders.state import FINAL
from gharbasket.platform import files

__all__ = [
    "CANCELLED",
    "CARRY_OVER_ATTACHED",
    "CARRY_OVER_EXPIRED",
    "DELIVERED",
    "DELIVERY_FAILED",
    "DISPATCHED",
    "LOCKED",
    "NOT_SOLD",
    "PACKED",
    "PAYMENT_FAILED",
    "SOLD",
    "TIFFIN_BOOKED",
    "ApprovedCycle",
    "CarriedLine",
    "CarryOver",
    "CutoffCounts",
    "CycleLine",
    "IssueFact",
    "Move",
    "Order",
    "OrderFact",
    "OrderIssue",
    "OrderLine",
    "OrderRef",
    "OrderSummary",
    "PaymentFailedOrder",
    "SlotRef",
    "SoldLine",
    "anonymise_for_user",
    "box_fee",
    "cancel_from_cycle",
    "carried_lines",
    "carry_over",
    "carry_target",
    "create_from_cycle",
    "cutoff_for",
    "export_for_user",
    "first_delivery_date",
    "get",
    "get_order_ref",
    "grace_until_for",
    "has_delivered_box",
    "has_open_issues",
    "has_open_orders",
    "hub_cutoff_counts",
    "issue_facts",
    "last_move",
    "mark_shortage",
    "order_facts",
    "order_ids_of_household",
    "orders_for_delivery",
    "refund_carry",
    "refundable_paise",
    "resolve_issue",
    "set_kitchen",
    "slot_ref",
    "sold_lines",
    "summaries",
    "transition",
]


async def get(session: AsyncSession, order_id: uuid.UUID, *, for_update: bool = False) -> Order:
    return await service.get(session, order_id, for_update=for_update)


async def has_delivered_box(session: AsyncSession, household_id: uuid.UUID) -> bool:
    """Whether the household has had a box delivered (M9: the referral claim window closes)."""
    found = await session.execute(
        select(Order.id)
        .where(Order.household_id == household_id, Order.kind == "box", Order.status == "delivered")
        .limit(1)
    )
    return found.first() is not None


async def cancel_from_cycle(session: AsyncSession, cycle_id: uuid.UUID) -> Order | None:
    """A skipped cycle cancels its order if one exists and is not paid yet (a `payment_failed`
    one too: M3 allows that skip until the grace ends)."""
    order = await service.by_cycle(session, cycle_id, for_update=True)
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
    active captain of its society, kitchen of its assignment).

    A move to `refunded` returns money to the owner's wallet through `billing.api`:
    `payload.refund_paise` (0 up to `refundable_paise`, the paid total less everything already
    paid back; default all of it), keyed by order and `payload.reason` (default `undelivered`;
    `no_kitchen` for a locked tiffin no kitchen took, ADR-0027), so a replay never pays twice."""
    order = await service.get(session, order_id, for_update=True)
    return await service.transition(
        session, order, to_status, actor=actor, actor_user_id=actor_user_id, payload=payload
    )


async def summaries(session: AsyncSession, order_ids: list[uuid.UUID]) -> list[OrderSummary]:
    """Orders with their lines and carried items, by id (M7's redeliveries, which ride on a
    later day)."""
    out = []
    riding = await carry.carried_for(session, order_ids)
    for order_id in order_ids:
        order = await service.get(session, order_id)
        out.append(OrderSummary(order, await service.lines_of(session, order_id), riding[order_id]))
    return out


async def carry_over(
    session: AsyncSession, order_id: uuid.UUID, lines: Sequence[Mapping[str, Any]]
) -> list[CarryOver]:
    """M7's second miss (M4 §3.8, ADR-0027 §2), in the transaction that moved the order to
    `refunded`: keep each line (`product_id`, `steps`, `paid_paise`) for the household's next
    box or add-on order, attached at once when one is already locked. Once per (order, product).
    The order must be a refunded box or add-on order (else `ValueError`)."""
    return await carry.carry_over(session, order_id, lines)


async def carried_lines(session: AsyncSession, order_id: uuid.UUID) -> list[CarriedLine]:
    """The items in that order's returned bag (M6's scan, M6 §3.5): carried from it, or carried
    in it and back after its own second miss; neither packed nor refunded yet."""
    return await carry.carried_lines(session, order_id)


async def carry_target(session: AsyncSession, carry_id: uuid.UUID) -> uuid.UUID | None:
    """The order a carry row is attached to (or riding, packed), if any: M6's lost-item action
    locks that order's packing task before it calls `refund_carry`. A plain read."""
    return await session.scalar(select(CarryOver.to_order_id).where(CarryOver.id == carry_id))


async def refund_carry(
    session: AsyncSession, carry_id: uuid.UUID, actor_user_id: uuid.UUID | None = None
) -> CarryOver:
    """A lost kept item (M6's hub manager, M4 §3.8): refund a pending or attached row at its paid
    price, no fee, keyed per row. Refunded already: as is. Packed or delivered: 409
    `orders.carry_not_refundable` with `current_status`. `billing.wallet_limit` is raised as is."""
    return await carry.refund_carry(session, carry_id, actor_user_id)


async def refundable_paise(session: AsyncSession, order_id: uuid.UUID) -> int:
    """What a refund of the order may still pay back (M7's triage bound)."""
    return await service.refundable_paise(session, await service.get(session, order_id))


@dataclass(frozen=True)
class OrderRef:
    id: uuid.UUID
    household_id: uuid.UUID
    owner_user_id: uuid.UUID | None  # the household's owner: the person who may rate or report
    kind: str
    status: str
    delivered_at: datetime | None  # when it reached `delivered`


async def get_order_ref(session: AsyncSession, order_id: uuid.UUID) -> OrderRef | None:
    """What M8's ratings need of an order (M8 §3.5): whose it is and when it was delivered."""
    order = await session.get(Order, order_id)
    if order is None:
        return None
    delivered_at = await session.scalar(
        select(func.max(OrderEvent.at)).where(
            OrderEvent.order_id == order_id, OrderEvent.to_status == "delivered"
        )
    )
    return OrderRef(
        id=order.id,
        household_id=order.household_id,
        owner_user_id=await identity_api.owner_of_household(session, order.household_id),
        kind=order.kind,
        status=order.status,
        delivered_at=delivered_at,
    )


@dataclass(frozen=True)
class Move:
    actor: str
    actor_user_id: uuid.UUID | None
    at: datetime


async def last_move(
    session: AsyncSession,
    order_id: uuid.UUID,
    to_status: str,
    event_id: uuid.UUID | None = None,
) -> Move | None:
    """Who moved the order into `to_status`, and when (the timeline row). `event_id` is the
    `order_event_id` of the fulfilment event being handled: it picks that very row, which matters
    when the status was reached twice. Without it, the latest row. M8's earnings read the person
    here: the fulfilment events carry the actor kind only (ADR-0026 §2)."""
    stmt = select(OrderEvent.actor, OrderEvent.actor_user_id, OrderEvent.at).where(
        OrderEvent.order_id == order_id, OrderEvent.to_status == to_status
    )
    if event_id is not None:
        stmt = stmt.where(OrderEvent.id == event_id)
    row = (await session.execute(stmt.order_by(OrderEvent.id.desc()).limit(1))).first()
    return None if row is None else Move(row[0], row[1], row[2])


async def set_kitchen(
    session: AsyncSession, order_id: uuid.UUID, partner_id: uuid.UUID | None
) -> Order:
    """Record the kitchen partner that cooks a tiffin (M8 on accept), or clear it with None (M8
    on a cancellation after acceptance), ADR-0026 §3; the order shows it as `kitchen`. Tiffins
    only (else `orders.not_editable`); the same kitchen again changes nothing."""
    order = await service.get(session, order_id, for_update=True)
    return await service.set_kitchen(session, order, partner_id)


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
    nothing is recorded. A household that chose "never substitute, refund" (M1 §3.4) is
    refused any substitute (`orders.substitute_invalid`, reason `refund_preferred`)."""
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
    meal: str | None = None  # lunch or dinner for a tiffin slot (§3.5)


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
        slot.id,
        slot.hub_id,
        slot.label,
        slot.kind,
        slot.start_time,
        slot.end_time,
        slot.active,
        slot.meal,
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
    (at most the issue's amount, or the food total for late/other, and never more than is left
    of what was paid for the order: `orders.issue_credit_invalid` carries `max_paise`) or reject
    it. The caller has checked `order_issues:resolve` for the issue's hub; `billing.wallet_limit`
    is raised as is."""
    return await issues.resolve(session, issue_id, outcome, credit_paise, actor, note)


async def export_for_user(session: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
    """M4's section of the user's data export (M1 §3.3), read-only: orders with lines and
    timelines, issues (their photos counted, never included) and carried items."""
    household = await identity_api.household_for_user(session, user_id)
    if household is None:
        return {"orders": [], "issues": [], "carry_overs": []}
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
        "carry_overs": await carry.export(session, household.id),
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


async def has_open_orders(session: AsyncSession, household_id: uuid.UUID) -> bool:
    """Whether an order of the household is not final yet, or a carried item still waits or
    rides (M4 §3.8): M1's purge waits (M1 §3.5)."""
    open_order = exists().where(Order.household_id == household_id, Order.status.not_in(FINAL))
    return bool(await session.scalar(select(open_order))) or await carry.has_open(
        session, household_id
    )


async def has_open_issues(session: AsyncSession, household_id: uuid.UUID) -> bool:
    """Whether an issue of the household is still `open`: M1's purge waits (M1 §3.5)."""
    open_issue = exists().where(
        OrderIssue.household_id == household_id, OrderIssue.status == "open"
    )
    return bool(await session.scalar(select(open_issue)))


async def order_ids_of_household(session: AsyncSession, household_id: uuid.UUID) -> list[uuid.UUID]:
    """Every order of the household, for M6's part of M1's purge (M1 §3.5)."""
    return list(await session.scalars(select(Order.id).where(Order.household_id == household_id)))


async def anonymise_for_user(
    session: AsyncSession, user_id: uuid.UUID, household_id: uuid.UUID | None
) -> dict[str, int]:
    """M1's purge (M1 §3.5, order 8, and §3.7 here): issue photos, notes and the hub's
    resolution notes (they may quote the customer) go; orders, lines,
    events and issue amounts stay (they back ledger entries), and events the user made as a
    partner lose the user id."""
    now = datetime.now(UTC)
    found: list[OrderIssue] = []
    if household_id is not None:
        found = list(
            await session.scalars(
                select(OrderIssue).where(
                    OrderIssue.household_id == household_id,
                    or_(
                        OrderIssue.note.is_not(None),
                        OrderIssue.resolution_note.is_not(None),
                        func.cardinality(OrderIssue.photo_keys) > 0,
                    ),
                )
            )
        )
    for issue in found:
        for key in issue.photo_keys:
            await files.set_delete_after(session, key, now)  # sweep_uploads deletes the file
        issue.note, issue.resolution_note, issue.photo_keys = None, None, []
    events = await session.execute(
        update(OrderEvent).where(OrderEvent.actor_user_id == user_id).values(actor_user_id=None)
    )
    return {
        "order_issues": len(found),
        "order_events": events.rowcount,  # pyright: ignore[reportAttributeAccessIssue]
    }
