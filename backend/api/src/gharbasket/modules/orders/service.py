"""Business logic of M4 Orders & Checkout (spec §3.1): slots, orders from approved cycles,
add-ons, cancellation, the timeline, and the transitions other modules drive.

The cut-off is a property of the delivery date: 21:00 IST on the day before. Edits are refused
here by the clock, so a late `cutoff_lock` job never lets one through.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, tuple_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.orders import state
from gharbasket.modules.orders.events import SHORTAGE_SUBSTITUTED
from gharbasket.modules.orders.models import Order, OrderEvent, OrderLine, Slot, SlotBooking
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.outbox import publish
from gharbasket.platform.pagination import decode_cursor, encode_cursor

IST = ZoneInfo("Asia/Kolkata")
CUTOFF_TIME = time(21, 0)
SLOT_DAYS_AHEAD = 14
MAX_ADDON_LINES = 20
MAX_ADDON_QTY = 20


def cutoff_for(delivery_date: date) -> datetime:
    """21:00 IST on the day before delivery, as a UTC instant."""
    return datetime.combine(delivery_date - timedelta(days=1), CUTOFF_TIME, IST).astimezone(UTC)


def now() -> datetime:
    return datetime.now(UTC)


def order_not_found() -> ProblemError:
    return ProblemError(404, "orders.not_found", "No such order.")


def cutoff_passed() -> ProblemError:
    return ProblemError(409, "orders.cutoff_passed", "The cut-off for this delivery has passed.")


# --- slots --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SlotLoad:
    slot: Slot
    booked: int

    @property
    def available(self) -> int:
        return max(0, self.slot.capacity - self.booked)


async def slots_on(session: AsyncSession, hub_id: uuid.UUID, on: date) -> list[SlotLoad]:
    today = datetime.now(IST).date()
    if not today <= on <= today + timedelta(days=SLOT_DAYS_AHEAD):
        raise ProblemError(
            422,
            "orders.date_out_of_range",
            f"Slots are shown for today to {SLOT_DAYS_AHEAD} days ahead.",
        )
    booked = (
        select(SlotBooking.slot_id, func.count().label("n"))
        .where(SlotBooking.on_date == on)
        .group_by(SlotBooking.slot_id)
        .subquery()
    )
    rows = await session.execute(
        select(Slot, func.coalesce(booked.c.n, 0))
        .outerjoin(booked, booked.c.slot_id == Slot.id)
        .where(Slot.hub_id == hub_id, Slot.active)
        .order_by(Slot.start_time, Slot.label)
    )
    return [SlotLoad(slot, int(n)) for slot, n in rows.all()]


async def get_slot(session: AsyncSession, slot_id: uuid.UUID, *, for_update: bool = False) -> Slot:
    stmt = select(Slot).where(Slot.id == slot_id)
    if for_update:
        stmt = stmt.with_for_update()
    slot = (await session.execute(stmt)).scalar_one_or_none()
    if slot is None:
        raise ProblemError(422, "orders.slot_invalid", "No such slot.")
    return slot


async def create_slot(
    session: AsyncSession,
    *,
    hub_id: uuid.UUID,
    label: str,
    kind: str,
    start: time,
    end: time,
    capacity: int,
) -> Slot:
    if end <= start:
        raise ProblemError(422, "orders.slot_invalid", "The slot must end after it starts.")
    slot = Slot(
        hub_id=hub_id, label=label, kind=kind, start_time=start, end_time=end, capacity=capacity
    )
    try:
        async with session.begin_nested():
            session.add(slot)
            await session.flush()
    except IntegrityError:
        raise ProblemError(
            409, "orders.slot_exists", "This hub already has a slot with that label."
        ) from None
    await session.refresh(slot)
    return slot


async def book_slot(
    session: AsyncSession, slot_id: uuid.UUID, on: date, order_id: uuid.UUID
) -> Slot:
    """Reserve one place in a slot for a date, under a row lock on the slot."""
    slot = await get_slot(session, slot_id, for_update=True)
    if not slot.active:
        raise ProblemError(422, "orders.slot_invalid", "This slot is closed.")
    taken = (
        await session.execute(
            select(func.count()).where(SlotBooking.slot_id == slot_id, SlotBooking.on_date == on)
        )
    ).scalar_one()
    if taken >= slot.capacity:
        raise ProblemError(
            409,
            "orders.slot_full",
            "This slot is full for that date.",
            extra={"slot_id": str(slot_id)},
        )
    session.add(SlotBooking(slot_id=slot_id, on_date=on, order_id=order_id))
    await session.flush()
    return slot


# --- orders -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CycleLine:
    product_id: uuid.UUID
    qty: int
    unit_price_paise: int
    reason_code: str | None = None
    reason_params: dict[str, Any] | None = None


@dataclass(frozen=True)
class ApprovedCycle:
    """What M3 hands over on `subscriptions.box_approved` (spec §3.1)."""

    cycle_id: uuid.UUID
    household_id: uuid.UUID
    hub_id: uuid.UUID
    society_id: uuid.UUID
    delivery_date: date
    slot_id: uuid.UUID
    lines: tuple[CycleLine, ...]
    data_source: str = "real"
    version: int = 1  # the cycle's version; an edit after approval hands the box over again


def _totals(order: Order, lines: list[OrderLine]) -> None:
    order.subtotal_paise = sum(line.total_paise for line in lines)
    order.total_paise = max(0, order.subtotal_paise + order.fees_paise - order.discount_paise)


async def by_cycle(session: AsyncSession, cycle_id: uuid.UUID) -> Order | None:
    return (
        await session.execute(select(Order).where(Order.cycle_id == cycle_id))
    ).scalar_one_or_none()


async def _box_products(
    session: AsyncSession, cycle: ApprovedCycle
) -> dict[uuid.UUID, catalog_api.LineProduct]:
    if not cycle.lines:
        raise ValueError("a box needs at least one line")
    products = await catalog_api.line_products(session, [line.product_id for line in cycle.lines])
    unknown = [str(line.product_id) for line in cycle.lines if line.product_id not in products]
    if unknown:
        raise ProblemError(
            422,
            "orders.product_unknown",
            "A line names an unknown product.",
            extra={"product_ids": unknown},
        )
    return products


def _box_lines(
    order: Order, cycle: ApprovedCycle, products: dict[uuid.UUID, catalog_api.LineProduct]
) -> list[OrderLine]:
    return [
        OrderLine(
            order_id=order.id,
            product_id=line.product_id,
            kind="box",
            qty=line.qty,
            step_qty=products[line.product_id].step_qty,
            step_unit=products[line.product_id].step_unit,
            unit_price_paise=line.unit_price_paise,
            reason_code=line.reason_code,
            reason_params=line.reason_params,
        )
        for line in cycle.lines
    ]


async def _replace_box_lines(session: AsyncSession, order: Order, cycle: ApprovedCycle) -> None:
    """Swap the box lines for the cycle's; the same lines again (a redelivered event) change
    nothing."""
    current = await lines_of(session, order.id)
    wanted = {
        (line.product_id, line.qty, line.unit_price_paise, line.reason_code) for line in cycle.lines
    }
    if {
        (line.product_id, line.qty, line.unit_price_paise, line.reason_code)
        for line in current
        if line.kind == "box"
    } == wanted:
        return
    products = await _box_products(session, cycle)
    kept = []
    for line in current:
        if line.kind == "box":
            await session.delete(line)
        else:
            kept.append(line)
    await session.flush()
    lines = _box_lines(order, cycle, products)
    session.add_all(lines)
    _totals(order, [*lines, *kept])
    order.version += 1
    session.add(
        OrderEvent(
            order_id=order.id,
            from_status="approved",
            to_status="approved",
            actor="customer",
            payload={"cycle_version": cycle.version},
        )
    )
    await session.flush()


async def create_from_cycle(session: AsyncSession, cycle: ApprovedCycle) -> Order:
    """An `approved` box order with the cycle's lines and frozen prices; once per cycle.

    Called again for the same cycle (an edit after approval) while the order is still
    `approved`, it replaces the box lines and totals; add-ons are kept (M4 §3.1).
    """
    existing = await by_cycle(session, cycle.cycle_id)
    if existing is not None:
        if existing.status == "approved":
            await _replace_box_lines(session, existing, cycle)
        return existing
    products = await _box_products(session, cycle)
    order = Order(
        household_id=cycle.household_id,
        hub_id=cycle.hub_id,
        society_id=cycle.society_id,
        kind="box",
        cycle_id=cycle.cycle_id,
        delivery_date=cycle.delivery_date,
        slot_id=cycle.slot_id,
        cutoff_at=cutoff_for(cycle.delivery_date),
        status="approved",
        subtotal_paise=0,
        fees_paise=0,
        discount_paise=0,
        total_paise=0,
        data_source=cycle.data_source,
    )
    try:
        async with session.begin_nested():  # all or nothing: a full slot leaves no order behind
            session.add(order)
            await session.flush()
            lines = _box_lines(order, cycle, products)
            session.add_all(lines)
            _totals(order, lines)
            await book_slot(session, cycle.slot_id, cycle.delivery_date, order.id)
            session.add(
                OrderEvent(
                    order_id=order.id, from_status=None, to_status="approved", actor="customer"
                )
            )
            await session.flush()
    except IntegrityError:  # the same cycle, at the same moment
        found = await by_cycle(session, cycle.cycle_id)
        if found is None:
            raise
        return found
    await session.refresh(order)
    return order


async def get(session: AsyncSession, order_id: uuid.UUID, *, for_update: bool = False) -> Order:
    stmt = select(Order).where(Order.id == order_id)
    if for_update:
        stmt = stmt.with_for_update()
    order = (await session.execute(stmt)).scalar_one_or_none()
    if order is None:
        raise order_not_found()
    return order


async def lines_of(session: AsyncSession, order_id: uuid.UUID) -> list[OrderLine]:
    rows = await session.execute(
        select(OrderLine)
        .where(OrderLine.order_id == order_id)
        .order_by(OrderLine.kind, OrderLine.product_id)
    )
    return list(rows.scalars())


async def timeline_of(session: AsyncSession, order_id: uuid.UUID) -> list[OrderEvent]:
    rows = await session.execute(
        select(OrderEvent).where(OrderEvent.order_id == order_id).order_by(OrderEvent.id)
    )
    return list(rows.scalars())


async def list_orders(
    session: AsyncSession,
    *,
    household_id: uuid.UUID | None,
    hub_id: uuid.UUID | None,
    status: str | None,
    from_date: date | None,
    to_date: date | None,
    cursor: str | None,
    limit: int,
) -> tuple[list[Order], str | None]:
    """Newest delivery first (then newest order), keyset paginated."""
    stmt = select(Order)
    if household_id is not None:
        stmt = stmt.where(Order.household_id == household_id)
    if hub_id is not None:
        stmt = stmt.where(Order.hub_id == hub_id)
    if status:
        stmt = stmt.where(Order.status == status)
    if from_date:
        stmt = stmt.where(Order.delivery_date >= from_date)
    if to_date:
        stmt = stmt.where(Order.delivery_date <= to_date)
    if cursor is not None:
        after = decode_cursor(cursor).get("after")
        try:
            if not (isinstance(after, list) and len(after) == 2):
                raise ValueError
            last_date, last_id = date.fromisoformat(str(after[0])), uuid.UUID(str(after[1]))  # pyright: ignore[reportUnknownArgumentType]
        except ValueError as exc:
            raise ProblemError(422, "request.invalid_cursor", "The cursor is not valid.") from exc
        stmt = stmt.where(tuple_(Order.delivery_date, Order.id) < tuple_(last_date, last_id))
    rows = list(
        (
            await session.execute(
                stmt.order_by(Order.delivery_date.desc(), Order.id.desc()).limit(limit + 1)
            )
        ).scalars()
    )
    found = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        last = found[-1]
        next_cursor = encode_cursor({"after": [last.delivery_date.isoformat(), str(last.id)]})
    return found, next_cursor


def _editable(order: Order) -> None:
    if order.status != "approved":
        raise ProblemError(409, "orders.not_editable", "Only an approved order can be changed.")
    if now() >= order.cutoff_at:
        raise cutoff_passed()


async def set_addons(
    session: AsyncSession, order: Order, items: dict[uuid.UUID, int], *, expected_version: int
) -> list[OrderLine]:
    """Add, change or remove (qty 0) add-on lines, priced from the hub's current list now."""
    if order.version != expected_version:
        raise ProblemError(412, "request.version_mismatch", "The order changed; reload it.")
    _editable(order)
    lines = await lines_of(session, order.id)
    by_product = {line.product_id: line for line in lines}
    for product_id, qty in items.items():
        current = by_product.get(product_id)
        if current is not None and current.kind == "box":
            raise ProblemError(
                422, "orders.qty_invalid", "Box lines are edited in the week's box, not as add-ons."
            )
        if not 0 <= qty <= MAX_ADDON_QTY:
            raise ProblemError(422, "orders.qty_invalid", f"Quantity must be 0 to {MAX_ADDON_QTY}.")
    wanted = {pid: qty for pid, qty in items.items() if qty > 0}
    products = await catalog_api.line_products(session, list(wanted), hub_id=order.hub_id)
    unknown = [str(pid) for pid in wanted if pid not in products]
    if unknown:
        raise ProblemError(
            422,
            "orders.product_unknown",
            "An add-on names an unknown product.",
            extra={"product_ids": unknown},
        )
    unpriced = [str(pid) for pid in wanted if products[pid].price_paise is None]
    if unpriced:
        raise ProblemError(
            422,
            "orders.no_price",
            "No price at your hub for an add-on.",
            extra={"product_ids": unpriced},
        )
    for product_id, qty in items.items():
        current = by_product.get(product_id)
        if qty == 0:
            if current is not None:
                await session.delete(current)
                lines.remove(current)
            continue
        if current is None:
            current = OrderLine(
                order_id=order.id,
                product_id=product_id,
                kind="addon",
                qty=qty,
                step_qty=products[product_id].step_qty,
                step_unit=products[product_id].step_unit,
                unit_price_paise=products[product_id].price_paise or 0,
            )
            session.add(current)
            lines.append(current)
        else:
            current.qty = qty
            current.unit_price_paise = products[product_id].price_paise or 0
    addons = [line for line in lines if line.kind == "addon"]
    if len(addons) > MAX_ADDON_LINES:
        raise ProblemError(422, "orders.qty_invalid", f"At most {MAX_ADDON_LINES} add-on lines.")
    _totals(order, lines)
    order.version += 1
    await session.flush()
    return lines


async def cancel(session: AsyncSession, order: Order, *, actor: str, reason: str) -> Order:
    """Before the cut-off, while approved: cancelled, booking freed, event published."""
    if order.status in ("cancelled",):
        return order
    if order.status in state.PAID:
        raise ProblemError(409, "orders.already_locked", "This order is already locked.")
    if order.status == "approved" and actor == "customer" and now() >= order.cutoff_at:
        raise cutoff_passed()
    await transition(session, order, "cancelled", actor=actor, payload={"reason": reason})
    booking = (
        await session.execute(select(SlotBooking).where(SlotBooking.order_id == order.id))
    ).scalar_one_or_none()
    if booking is not None:
        await session.delete(booking)
        await session.flush()
    return order


async def transition(
    session: AsyncSession,
    order: Order,
    to_status: str,
    *,
    actor: str,
    payload: dict[str, Any] | None = None,
) -> Order:
    """Move an order along the state machine, write the timeline row and publish the event."""
    state.check(order.status, to_status, actor)
    from_status = order.status
    order.status = to_status
    order.version += 1
    if to_status == "locked":
        order.locked_at = now()
    session.add(
        OrderEvent(
            order_id=order.id,
            from_status=from_status,
            to_status=to_status,
            actor=actor,
            payload=payload,
        )
    )
    await session.flush()
    event_type = state.EVENTS.get(to_status)
    if event_type is not None:
        body: dict[str, Any] = {
            "order_id": str(order.id),
            "household_id": str(order.household_id),
            "hub_id": str(order.hub_id),
            "society_id": str(order.society_id),
            "delivery_date": order.delivery_date.isoformat(),
            "slot_id": str(order.slot_id),
            "total_paise": order.total_paise,
            **(payload or {}),
        }
        if to_status == "locked":
            body["lines"] = [
                {
                    "product_id": str(line.product_id),
                    "qty": line.qty,
                    "step_qty": line.step_qty,
                    "step_unit": line.step_unit,
                }
                for line in await lines_of(session, order.id)
            ]
        await publish(session, event_type, body)
    return order


@dataclass(frozen=True)
class OrderSummary:
    order: Order
    lines: list[OrderLine]


async def orders_for_delivery(
    session: AsyncSession,
    hub_id: uuid.UUID,
    delivery_date: date,
    statuses: tuple[str, ...] = ("locked",),
) -> list[OrderSummary]:
    """What M6 packs and M7 routes on a day, with lines."""
    orders = list(
        (
            await session.execute(
                select(Order)
                .where(
                    Order.hub_id == hub_id,
                    Order.delivery_date == delivery_date,
                    Order.status.in_(statuses),
                )
                .order_by(Order.society_id, Order.id)
            )
        ).scalars()
    )
    if not orders:
        return []
    rows = await session.execute(
        select(OrderLine).where(OrderLine.order_id.in_([o.id for o in orders]))
    )
    by_order: dict[uuid.UUID, list[OrderLine]] = {o.id: [] for o in orders}
    for line in rows.scalars():
        by_order[line.order_id].append(line)
    return [OrderSummary(o, by_order[o.id]) for o in orders]


# Packing may record a shortage while the paid order is still at the hub.
PACKING = frozenset({"locked", "picking", "packed"})


def _substitute_invalid(reason: str) -> ProblemError:
    return ProblemError(
        422,
        "orders.substitute_invalid",
        "This product cannot replace the line.",
        extra={"reason": reason},
    )


async def _shortage_refund(
    session: AsyncSession, order: Order, product_id: uuid.UUID, amount_paise: int
) -> None:
    """Money back for a short line, once per line by M5's key `refund:<order>:short_<hex>`."""
    owner = await identity_api.owner_of_household(session, order.household_id)
    if owner is None:
        raise ValueError(f"order {order.id} has no household owner")
    async with session.begin_nested():  # a refused refund leaves nothing behind
        await billing_api.refund_to_wallet(
            session, owner, order.id, amount_paise, reason=f"short_{product_id.hex}"
        )


async def mark_shortage(
    session: AsyncSession,
    order: Order,
    product_id: uuid.UUID,
    shortage_qty: int,
    substitute_product_id: uuid.UUID | None,
) -> OrderLine:
    """Packing found fewer steps than ordered (M6, ADR-0026 §6), on a row-locked order that is
    still at the hub (`locked`, `picking`, `packed`; else `orders.not_editable`).

    A partial shortage is refunded (shortage_qty x the line's paid unit price), never partly
    substituted. A whole-line shortage may instead be replaced by one substitute from the
    catalog's list (`catalog.api.substitute_ids`) that is not already on the order and costs no
    more per step at the hub today: the line takes the substitute's product, step and price and
    keeps its qty, `substituted_from` names the original, and the difference is refunded.
    Anything else is `orders.substitute_invalid` (with `reason`), so the bag matches the record.

    The refund goes through `billing.api.refund_to_wallet(reason=f"short_{product_id.hex}")` in
    the caller's transaction, and the order's totals follow its lines (a line counts
    qty - shortage_qty steps), so they show what was paid net of refunds. A line takes one
    shortage: the same call again returns the line and moves no money (M5's key backs this up);
    a different one is `orders.shortage_recorded`. If the wallet cap refuses the refund,
    `billing.wallet_limit` is raised as is (as `resolve_issue` does) and nothing is recorded:
    a shortage is never kept without its refund.
    """
    lines = await lines_of(session, order.id)
    line = next((ln for ln in lines if ln.product_id == product_id), None) or next(
        (ln for ln in lines if ln.substituted_from == product_id), None
    )
    if line is None:
        raise order_not_found()
    if not 1 <= shortage_qty <= line.qty:
        raise ProblemError(
            422, "orders.qty_invalid", "A shortage is from 1 step to the line's quantity."
        )
    if line.shortage_qty or line.substituted_from is not None:
        same = (
            line.substituted_from == product_id
            and substitute_product_id == line.product_id
            and shortage_qty == line.qty
        ) or (substitute_product_id is None and shortage_qty == line.shortage_qty)
        if not same:
            raise ProblemError(
                409, "orders.shortage_recorded", "A shortage is already recorded for this line."
            )
        return line
    if order.status not in PACKING:
        raise ProblemError(
            409, "orders.not_editable", "Shortages are recorded while the order is being packed."
        )

    substitute: catalog_api.LineProduct | None = None
    if substitute_product_id is None:
        refund = shortage_qty * line.unit_price_paise
    else:
        found = await catalog_api.line_products(
            session, [substitute_product_id], hub_id=order.hub_id
        )
        substitute = found.get(substitute_product_id)
        if substitute is None:
            raise ProblemError(422, "orders.product_unknown", "Unknown substitute.")
        if shortage_qty != line.qty:
            raise _substitute_invalid("partial")
        if any(substitute.id in (ln.product_id, ln.substituted_from) for ln in lines):
            raise _substitute_invalid("on_order")
        if substitute.id not in await catalog_api.substitute_ids(session, product_id, order.hub_id):
            raise _substitute_invalid("not_a_substitute")
        if substitute.price_paise is None:
            raise _substitute_invalid("no_price")
        if substitute.price_paise > line.unit_price_paise:
            raise _substitute_invalid("pricier")
        refund = line.qty * (line.unit_price_paise - substitute.price_paise)
    if refund > 0:
        await _shortage_refund(session, order, product_id, refund)

    if substitute is None:
        line.shortage_qty = shortage_qty
    else:
        line.substituted_from = line.product_id
        line.product_id = substitute.id
        line.step_qty, line.step_unit = substitute.step_qty, substitute.step_unit
        line.unit_price_paise = substitute.price_paise or 0
        line.reason_code = line.reason_params = None  # the planner's reason was the original's
    _totals(order, lines)
    order.version += 1
    await session.flush()
    await publish(
        session,
        SHORTAGE_SUBSTITUTED,
        {
            "order_id": str(order.id),
            "product_id": str(product_id),
            "shortage_qty": shortage_qty,
            "substitute_product_id": None if substitute is None else str(substitute.id),
            "refund_paise": refund,
            "total_paise": order.total_paise,
        },
    )
    return line
