"""Carry-over (M4 §3.8, ADR-0027 §2 and §6): the non-perishable lines of a box missed twice ride,
free, on the household's next box or add-on order, or are refunded after 14 days.

A carried item is a row of `orders.carry_overs`, never an order line, so it cannot collide with a
paid line of the same product and does not count towards totals, debits, shortages or procurement.
A row is `pending` (waiting for a box), `attached` to a locked box, `packed` when that box is
packed, `delivered` with that box, or `refunded` (expiry or the hub). If its box is missed twice
too, it goes back to `pending` with its original expiry.

This module does not import `service`: `service.transition` calls `follow` for the hooks.
"""

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import and_, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.orders.events import CARRY_OVER_ATTACHED, CARRY_OVER_EXPIRED, CLOSED
from gharbasket.modules.orders.models import CarryOver, Order, OrderEvent, OrderLine
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.outbox import publish

log = structlog.get_logger("gharbasket.orders.carry")

CARRY_DAYS = 14
OPEN = ("pending", "attached", "packed")  # not settled: the purge waits, the hub may be asked
ON_A_BOX = ("attached", "packed")
RIDING = ("attached", "packed", "delivered")  # what an order shows as `carried`
IN_THE_BAG = ("pending", "attached")  # what a returned bag holds that nobody packed yet
BOX_KINDS = ("box", "addon")


@dataclass(frozen=True)
class CarriedLine:
    """One carried item, as M6 packs it (`OrderSummary.carried`) or unpacks it (`carried_lines`)."""

    carry_id: uuid.UUID
    carried_from_order_id: uuid.UUID
    product_id: uuid.UUID
    steps: int
    step_qty: int
    step_unit: str


def _line(row: CarryOver) -> CarriedLine:
    return CarriedLine(
        row.id, row.from_order_id, row.product_id, row.steps, row.step_qty, row.step_unit
    )


def _now() -> datetime:
    return datetime.now(UTC)


async def publish_closed(session: AsyncSession, order: Order) -> None:
    """`orders.closed` for a `refunded` order that has nothing left open (§3.8): M5 recognises what
    it still holds. The caller has decided that it is closed, once."""
    await publish(
        session,
        CLOSED,
        {
            "order_id": str(order.id),
            "household_id": str(order.household_id),
            "hub_id": str(order.hub_id),
        },
    )


def _closed_at() -> Any:
    """SQL: the moment a `refunded` order closed: its move to `refunded` (the order's own
    `updated_at` if that timeline row is missing), or its last carried row settling, whichever is
    later. Needs `OrderEvent` outer-joined on the `refunded` move."""
    settled = (
        select(func.max(CarryOver.settled_at))
        .where(CarryOver.from_order_id == Order.id)
        .scalar_subquery()
    )
    moved = func.coalesce(OrderEvent.at, Order.updated_at)
    return func.greatest(moved, func.coalesce(settled, moved))


def _closed_orders_query(*columns: Any) -> Any:
    return (
        select(*columns)
        .select_from(Order)
        .outerjoin(
            OrderEvent,
            and_(OrderEvent.order_id == Order.id, OrderEvent.to_status == "refunded"),
        )
        .where(
            Order.status == "refunded",
            ~exists().where(CarryOver.from_order_id == Order.id, CarryOver.status.in_(OPEN)),
        )
        .order_by(Order.id)
    )


async def closed_orders(
    session: AsyncSession, *, after: uuid.UUID | None = None, limit: int | None = None
) -> list[Order]:
    """`refunded` orders with nothing left open, by id, for `republish_closed_orders`."""
    query = _closed_orders_query(Order)
    if after is not None:
        query = query.where(Order.id > after)
    if limit is not None:
        query = query.limit(limit)
    return list(await session.scalars(query))


async def closed_order_ids(
    session: AsyncSession, closed_from: datetime, closed_until: datetime
) -> list[uuid.UUID]:
    """Ids of the `refunded` orders that closed in `[closed_from, closed_until)`, for the ops
    probe: the window is in SQL, so only the recent ones are read."""
    closed_at = _closed_at()
    query = _closed_orders_query(Order.id).where(closed_at >= closed_from, closed_at < closed_until)
    return list(await session.scalars(query))


async def close_settled(session: AsyncSession, order_ids: Iterable[uuid.UUID]) -> None:
    """Rows of these missed orders just settled (delivered, refunded): publish `orders.closed` for
    each order whose last row it was. The rows are flushed first and the order's row lock is taken
    before the check, so two rows settled at the same moment in two transactions cannot each see
    the other as open: the one that locks second sees the first's commit and publishes. A refunded
    order's row is final, so it closes once. Lock order, everywhere in M4 and M5: the carry row,
    the missed order's row, M5's order lock, the wallet's row; `refund_carry` and `expire` take
    the missed order's row before they refund, so this only waits for what they hold."""
    for order_id in sorted(set(order_ids)):
        order = await session.get(Order, order_id, with_for_update=True, populate_existing=True)
        if order is None or order.status != "refunded":
            continue
        still_open = await session.scalar(
            select(exists().where(CarryOver.from_order_id == order_id, CarryOver.status.in_(OPEN)))
        )
        if not still_open:
            await publish_closed(session, order)


async def carry_over(
    session: AsyncSession, order_id: uuid.UUID, lines: Sequence[Mapping[str, Any]]
) -> list[CarryOver]:
    """M7's second miss, after `undelivered -> refunded`: a `pending` row per line (`product_id`,
    `steps`, `paid_paise`), then attached at once if the household has a box that fits. Once per
    (order, product): a repeat returns the existing rows and changes nothing. A box or add-on
    order that is not `refunded`, or a line that is not on it, is a `ValueError`."""
    order = await session.get(Order, order_id)
    if order is None or order.kind not in BOX_KINDS or order.status != "refunded":
        raise ValueError(f"order {order_id} is not a refunded box or add-on order")
    rows = {
        row.product_id: row
        for row in await session.scalars(
            select(CarryOver).where(CarryOver.from_order_id == order_id)
        )
    }
    on_order = {
        line.product_id: line
        for line in await session.scalars(select(OrderLine).where(OrderLine.order_id == order_id))
    }
    out: list[CarryOver] = []
    for item in lines:
        product_id = uuid.UUID(str(item["product_id"]))
        row = rows.get(product_id)
        if row is None:
            line = on_order.get(product_id)
            steps, paid = int(item["steps"]), int(item["paid_paise"])
            if line is None or not 1 <= steps <= line.qty - line.shortage_qty or paid < 0:
                raise ValueError(f"order {order_id} cannot carry {steps} steps of {product_id}")
            row = CarryOver(
                from_order_id=order.id,
                household_id=order.household_id,
                hub_id=order.hub_id,
                society_id=order.society_id,
                product_id=product_id,
                steps=steps,
                step_qty=line.step_qty,
                step_unit=line.step_unit,
                paid_paise=paid,
                missed_on=order.delivery_date,
                expires_on=order.delivery_date + timedelta(days=CARRY_DAYS),
                status="pending",
            )
            session.add(row)
            rows[product_id] = row
        out.append(row)
    await session.flush()
    await attach_to_next(session, order.household_id, order.delivery_date)
    if not rows:  # a second miss that was to carry rows and carried none: nothing keeps it open
        await close_settled(session, [order_id])
    return out


async def attach_to_next(session: AsyncSession, household_id: uuid.UUID, after: date) -> None:
    """Attach the household's pending rows to its earliest box or add-on order that is already
    `locked` and delivers after `after`, if there is one."""
    target = await session.scalar(
        select(Order)
        .where(
            Order.household_id == household_id,
            Order.kind.in_(BOX_KINDS),
            Order.status == "locked",
            Order.delivery_date > after,
        )
        .order_by(Order.delivery_date, Order.id)
        .limit(1)
        .with_for_update()
    )
    if target is not None:
        await attach_pending(session, target)


async def attach_pending(session: AsyncSession, order: Order) -> list[CarryOver]:
    """Attach every `pending` row of the household whose miss came before this locked box or
    add-on order's delivery, oldest first, and publish `orders.carry_over_attached` once."""
    rows = list(
        await session.scalars(
            select(CarryOver)
            .where(
                CarryOver.household_id == order.household_id,
                CarryOver.status == "pending",
                CarryOver.missed_on < order.delivery_date,
            )
            .order_by(CarryOver.missed_on, CarryOver.created_at, CarryOver.id)
            .with_for_update()
        )
    )
    if not rows:
        return rows
    for row in rows:
        row.status, row.to_order_id = "attached", order.id
    await session.flush()
    await publish(
        session,
        CARRY_OVER_ATTACHED,
        {
            "order_id": str(order.id),
            "household_id": str(order.household_id),
            "hub_id": str(order.hub_id),
            "lines": [
                {
                    "carry_id": str(row.id),
                    "carried_from_order_id": str(row.from_order_id),
                    "product_id": str(row.product_id),
                    "steps": row.steps,
                }
                for row in rows
            ],
        },
    )
    return rows


async def follow(session: AsyncSession, order: Order, to_status: str) -> None:
    """The rows follow their order (`service.transition` calls this after every move of a box or
    add-on order): a lock attaches pending rows, `packed` packs the attached ones (while the box
    is `locked` or `picking` the hub can still report a kept item lost), delivery settles them,
    and a refund of the order gives them back, to ride on the next locked box if there is one.
    The caller holds the order (and, for M6, its packing task) before the rows are locked."""
    if order.kind not in BOX_KINDS:
        return
    if to_status == "locked":
        await attach_pending(session, order)
        return
    if to_status not in ("packed", "delivered", "refunded"):
        return
    # Locked, with the status filter re-checked after any wait: a hub refund that committed first
    # is not written over.
    rows = list(
        await session.scalars(
            select(CarryOver)
            .where(
                CarryOver.to_order_id == order.id,
                CarryOver.status.in_(("attached",) if to_status == "packed" else ON_A_BOX),
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    for row in rows:
        if to_status == "packed":
            row.status = "packed"
        elif to_status == "delivered":
            row.status, row.settled_at = "delivered", _now()
        else:  # refunded: the bag is back at the hub, the item waits for the next box
            row.status, row.to_order_id, row.returned_in_order_id = "pending", None, order.id
    await session.flush()
    if to_status == "refunded" and rows:
        await attach_to_next(session, order.household_id, min(r.missed_on for r in rows))
    if to_status == "delivered":  # the last row of a missed order closes it (§3.8)
        await close_settled(session, (row.from_order_id for row in rows))


async def delivering(session: AsyncSession, order_id: uuid.UUID) -> list[dict[str, Any]]:
    """The rows `follow` delivers with this order, for its `orders.delivered` (M5 recognises
    their revenue on the missed order, M5 §3.9). Locked now, so they are the rows it delivers."""
    rows = await session.scalars(
        select(CarryOver)
        .where(CarryOver.to_order_id == order_id, CarryOver.status.in_(ON_A_BOX))
        .order_by(CarryOver.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    return [
        {
            "carry_id": str(row.id),
            "from_order_id": str(row.from_order_id),
            "product_id": str(row.product_id),
            "paid_paise": row.paid_paise,
        }
        for row in rows
    ]


async def carried_for(
    session: AsyncSession, order_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, list[CarriedLine]]:
    """The rows riding on each order (`OrderSummary.carried`, the customer's `carried`)."""
    found: dict[uuid.UUID, list[CarriedLine]] = {i: [] for i in order_ids}
    if not found:
        return found
    rows = await session.scalars(
        select(CarryOver)
        .where(CarryOver.to_order_id.in_(list(found)), CarryOver.status.in_(RIDING))
        .order_by(CarryOver.missed_on, CarryOver.created_at, CarryOver.id)
    )
    for row in rows:
        if row.to_order_id is not None:
            found[row.to_order_id].append(_line(row))
    return found


async def carried_lines(session: AsyncSession, order_id: uuid.UUID) -> list[CarriedLine]:
    """The items in that order's returned bag (M6 §3.5): the rows carried from it, plus the rows
    that rode in it and came back, that nobody has packed or refunded."""
    rows = await session.scalars(
        select(CarryOver)
        .where(
            (CarryOver.from_order_id == order_id) | (CarryOver.returned_in_order_id == order_id),
            CarryOver.status.in_(IN_THE_BAG),
        )
        .order_by(CarryOver.missed_on, CarryOver.created_at, CarryOver.id)
    )
    return [_line(row) for row in rows]


async def state_of(session: AsyncSession, order_id: uuid.UUID) -> CarryOver | None:
    """The row that stands for a missed order: its oldest unsettled one, else its latest settled
    one; None when nothing was carried."""
    rows = list(
        await session.scalars(
            select(CarryOver)
            .where(CarryOver.from_order_id == order_id)
            .order_by(CarryOver.missed_on, CarryOver.created_at, CarryOver.id)
        )
    )
    unsettled = [row for row in rows if row.status in OPEN]
    if unsettled:
        return unsettled[0]
    return max(rows, key=lambda r: r.settled_at or r.created_at, default=None)


async def refunded_paise(session: AsyncSession, order_id: uuid.UUID) -> int:
    """What the order's carried items were refunded (counted by `service.refunded_paise`)."""
    total = await session.scalar(
        select(func.coalesce(func.sum(CarryOver.paid_paise), 0)).where(
            CarryOver.from_order_id == order_id, CarryOver.status == "refunded"
        )
    )
    return int(total or 0)


class NoOwner(ValueError):
    """The household has no owner to refund (a deleted account): nothing is changed."""


async def _refund(session: AsyncSession, row: CarryOver, by: str) -> int:
    """The row's paid price back to the wallet, no fee, keyed by order and product so a repeat
    moves no money; in a savepoint, so a refused refund (`billing.wallet_limit`) leaves no trace.
    Never more than is left to refund on the missed order (earlier credits count), so an order
    never returns more than was paid. Returns the amount paid back."""
    from gharbasket.modules.orders import service  # service imports this module

    order = await session.get(Order, row.from_order_id)
    assert order is not None
    amount = min(row.paid_paise, await service.refundable_paise(session, order))
    if amount > 0:
        owner = await identity_api.owner_of_household(session, row.household_id)
        if owner is None:
            raise NoOwner(f"household {row.household_id} has no owner")
        async with session.begin_nested():
            await billing_api.refund_to_wallet(
                session, owner, row.from_order_id, amount, reason=f"carry_{row.product_id.hex}"
            )
    row.status, row.refunded_by, row.settled_at = "refunded", by, _now()
    await session.flush()
    return amount


async def refund_carry(
    session: AsyncSession, carry_id: uuid.UUID, actor_user_id: uuid.UUID | None = None
) -> CarryOver:
    """A lost kept item (M6's hub manager): refund a `pending` or `attached` row at once. An
    already `refunded` row returns as is; a `packed` or `delivered` one is 409
    `orders.carry_not_refundable` with `current_status` (RFC 9457 keeps `status` for the HTTP
    code). `billing.wallet_limit` is raised as is."""
    row = await session.scalar(
        select(CarryOver)
        .where(CarryOver.id == carry_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise ProblemError(404, "orders.carry_not_found", "No such carried item.")
    if row.status == "refunded":
        return row
    # Lock order: the carry row (above), the missed order's row, then M5's order lock and the
    # wallet's row inside the refund; `close_settled` then finds the order row already held.
    await session.get(Order, row.from_order_id, with_for_update=True, populate_existing=True)
    if row.status not in ("pending", "attached"):
        raise ProblemError(
            409,
            "orders.carry_not_refundable",
            "This item is already on its way to the household.",
            extra={"current_status": row.status},
        )
    await _refund(session, row, "hub")
    await close_settled(session, [row.from_order_id])
    log.info("carry_refunded", carry_id=str(row.id), actor_user_id=str(actor_user_id))
    return row


async def expire(session: AsyncSession, day: date) -> dict[str, Any]:
    """The daily job's work: refund every `pending` row that expired before `day` (14 days after
    the miss), one savepoint each, and publish `orders.carry_over_expired` per missed order. A
    wallet that cannot take a refund (`wallet_limit`), or a household with no owner left
    (`no_owner`), leaves its row `pending`; the others are refunded all the same."""
    rows = list(
        await session.scalars(
            select(CarryOver)
            .where(CarryOver.status == "pending", CarryOver.expires_on < day)
            .order_by(CarryOver.from_order_id, CarryOver.id)
            .with_for_update(skip_locked=True)
        )
    )
    # Lock order, as `refund_carry`: carry rows (above), the missed orders' rows (by id), then M5's
    # order lock and the wallet's row inside each refund.
    for order_id in sorted({row.from_order_id for row in rows}):
        await session.get(Order, order_id, with_for_update=True, populate_existing=True)
    done: dict[uuid.UUID, list[CarryOver]] = {}
    paid = wallet_limit = no_owner = 0
    for row in rows:
        try:
            paid += await _refund(session, row, "expiry")
        except NoOwner:
            log.warning("carry_expiry_no_owner", carry_id=str(row.id))
            no_owner += 1
            continue
        except ProblemError as refused:
            if refused.code != "billing.wallet_limit":
                raise
            wallet_limit += 1
            continue
        done.setdefault(row.from_order_id, []).append(row)
    await close_settled(session, done)
    for order_id, refunded in done.items():
        first = refunded[0]
        await publish(
            session,
            CARRY_OVER_EXPIRED,
            {
                "order_id": str(order_id),
                "household_id": str(first.household_id),
                "hub_id": str(first.hub_id),
                "lines": [
                    {
                        "carry_id": str(r.id),
                        "product_id": str(r.product_id),
                        "steps": r.steps,
                        "paid_paise": r.paid_paise,
                    }
                    for r in refunded
                ],
                "reason": "carry_over_expired",
            },
        )
    return {
        "date": day.isoformat(),
        "refunded": sum(len(r) for r in done.values()),
        "refunded_paise": paid,
        "wallet_limit": wallet_limit,
        "no_owner": no_owner,
    }


async def has_open(session: AsyncSession, household_id: uuid.UUID) -> bool:
    """Whether the household has a row still to settle: M1's purge waits."""
    return bool(
        await session.scalar(
            select(
                exists().where(CarryOver.household_id == household_id, CarryOver.status.in_(OPEN))
            )
        )
    )


async def export(session: AsyncSession, household_id: uuid.UUID) -> list[dict[str, Any]]:
    """The household's rows for M1's data export."""
    rows = await session.scalars(
        select(CarryOver)
        .where(CarryOver.household_id == household_id)
        .order_by(CarryOver.missed_on, CarryOver.created_at, CarryOver.id)
    )
    return [row.as_dict(exclude={"household_id"}) for row in rows]
