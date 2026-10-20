"""Carry-over (M4 §3.8, ADR-0027 §2 and §6): the non-perishable lines of a box missed twice ride,
free, on the household's next box or add-on order, or are refunded after 14 days.

A carried item is a row of `orders.carry_overs`, never an order line, so it cannot collide with a
paid line of the same product and does not count towards totals, debits, shortages or procurement.
A row is `pending` (waiting for a box), `attached` to a locked box, `packed` when M6 packs it,
`delivered` with that box, or `refunded` (expiry or the hub). If its box is missed twice too, it
goes back to `pending` with its original expiry.

This module does not import `service`: `service.transition` calls `follow` for the hooks.
"""

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.orders.events import CARRY_OVER_ATTACHED, CARRY_OVER_EXPIRED
from gharbasket.modules.orders.models import CarryOver, Order, OrderLine
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.outbox import publish

log = structlog.get_logger("gharbasket.orders.carry")

CARRY_DAYS = 14
OPEN = ("pending", "attached", "packed")  # not settled: the purge waits, the hub may be asked
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
    target = await session.scalar(
        select(Order)
        .where(
            Order.household_id == order.household_id,
            Order.kind.in_(BOX_KINDS),
            Order.status == "locked",
            Order.delivery_date > order.delivery_date,
        )
        .order_by(Order.delivery_date, Order.id)
        .limit(1)
        .with_for_update()
    )
    if target is not None:
        await attach_pending(session, target)
    return out


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
    add-on order): a lock attaches pending rows, picking or packed packs the attached ones,
    delivery settles them, and a refund of the order (missed twice) gives them back."""
    if order.kind not in BOX_KINDS:
        return
    if to_status == "locked":
        await attach_pending(session, order)
        return
    if to_status not in ("picking", "packed", "delivered", "refunded"):
        return
    rows = await session.scalars(
        select(CarryOver).where(
            CarryOver.to_order_id == order.id, CarryOver.status.in_(("attached", "packed"))
        )
    )
    for row in rows:
        if to_status in ("picking", "packed"):
            row.status = "packed"
        elif to_status == "delivered":
            row.status, row.settled_at = "delivered", _now()
        else:  # refunded: the bag is back at the hub, the item waits for the next box
            row.status, row.to_order_id, row.returned_in_order_id = "pending", None, order.id
    await session.flush()


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


async def _refund(session: AsyncSession, row: CarryOver, by: str) -> None:
    """The row's paid price back to the wallet, no fee, keyed by order and product so a repeat
    moves no money; in a savepoint, so a refused refund (`billing.wallet_limit`) leaves no trace."""
    if row.paid_paise > 0:
        owner = await identity_api.owner_of_household(session, row.household_id)
        if owner is None:
            raise ValueError(f"household {row.household_id} has no owner")
        async with session.begin_nested():
            await billing_api.refund_to_wallet(
                session,
                owner,
                row.from_order_id,
                row.paid_paise,
                reason=f"carry_{row.product_id.hex}",
            )
    row.status, row.refunded_by, row.settled_at = "refunded", by, _now()
    await session.flush()


async def refund_carry(
    session: AsyncSession, carry_id: uuid.UUID, actor_user_id: uuid.UUID | None = None
) -> CarryOver:
    """A lost kept item (M6's hub manager): refund a `pending` or `attached` row at once. An
    already `refunded` row returns as is; a `packed` or `delivered` one is 409
    `orders.carry_not_refundable` with `current_status` (RFC 9457 keeps `status` for the HTTP
    code). `billing.wallet_limit` is raised as is."""
    row = await session.scalar(select(CarryOver).where(CarryOver.id == carry_id).with_for_update())
    if row is None:
        raise ProblemError(404, "orders.carry_not_found", "No such carried item.")
    if row.status == "refunded":
        return row
    if row.status not in ("pending", "attached"):
        raise ProblemError(
            409,
            "orders.carry_not_refundable",
            "This item is already on its way to the household.",
            extra={"current_status": row.status},
        )
    await _refund(session, row, "hub")
    log.info("carry_refunded", carry_id=str(row.id), actor_user_id=str(actor_user_id))
    return row


async def expire(session: AsyncSession, day: date) -> dict[str, Any]:
    """The daily job's work: refund every `pending` row that expired before `day` (14 days after
    the miss), one savepoint each, and publish `orders.carry_over_expired` per missed order. A
    wallet that cannot take a refund leaves its row `pending` for the next run."""
    rows = list(
        await session.scalars(
            select(CarryOver)
            .where(CarryOver.status == "pending", CarryOver.expires_on < day)
            .order_by(CarryOver.from_order_id, CarryOver.id)
            .with_for_update(skip_locked=True)
        )
    )
    done: dict[uuid.UUID, list[CarryOver]] = {}
    wallet_limit = 0
    for row in rows:
        try:
            await _refund(session, row, "expiry")
        except ProblemError as refused:
            if refused.code != "billing.wallet_limit":
                raise
            wallet_limit += 1
            continue
        done.setdefault(row.from_order_id, []).append(row)
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
        "refunded_paise": sum(r.paid_paise for rs in done.values() for r in rs),
        "wallet_limit": wallet_limit,
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
