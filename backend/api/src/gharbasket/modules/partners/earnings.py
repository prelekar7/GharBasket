"""What partners earn (spec §3.6): M8 decides who earns what for which event, from the hub's rate
card, and M5 records it in the ledger; the view reads it back from M5.

Consumers of M4's fulfilment events post each earning in the consumer's transaction through
`billing.api.accrue_earning`, keyed by partner, basis and order, so a redelivered event never pays
twice. The rate is the one valid on the order's delivery date at its hub. A later refund or issue
does not claw an earning back. M8 keeps no earnings table.

| Kind    | Basis            | Event                                          | Units    |
| kitchen | tiffin_portion   | `orders.delivered`, a tiffin: the order's cook | portions |
| captain | bag_delivered    | `orders.delivered`, a box or add-on, by her    | 1 bag    |
| rider   | dispatched_order | `orders.dispatched`, by her                    | 1 order  |
| packer  | packed_order     | `orders.packed`, by her                        | 1 order  |

An order a `hub` or `system` actor moved earns the field partner nothing; a captain earns nothing
for a tiffin she delivers (ADR-0027).
"""

import re
import uuid
from datetime import date, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.partners import kitchens, rates
from gharbasket.modules.partners.models import BASIS_OF_KIND, Assignment, AssignmentOrder, Partner
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.models import OutboxEvent

# The field role whose move earns, per event: (kind of partner, the actor kind that earns it).
FIELD = {
    orders_api.DISPATCHED: ("rider", "rider"),
    orders_api.PACKED: ("packer", "packer"),
}
WEEK = re.compile(r"^(\d{4})-W(\d{2})$")
log = structlog.get_logger("gharbasket.partners")


async def pay(
    session: AsyncSession,
    partner: Partner,
    *,
    order_id: uuid.UUID,
    hub_id: uuid.UUID,
    units: int,
    on: date,
    earned_at: datetime,
    occurrence: uuid.UUID | None = None,
) -> int:
    """Accrue `units` of her basis at the order's hub rate for the delivery date `on`; the paise.
    A repeat of the same trip that the ledger already holds with other amounts (a rate changed
    in between) is logged and skipped: it must never fail the event, which would retry forever."""
    rate = await rates.rate_on(session, hub_id, partner.kind, on)
    amount = units * rate
    if amount > 0:  # a rate of 0 earns nothing
        try:
            await billing_api.accrue_earning(
                session,
                partner_id=partner.id,
                basis=BASIS_OF_KIND[partner.kind],
                ref_type="order",
                ref_id=order_id,
                units=units,
                amount_paise=amount,
                earned_at=earned_at,
                occurrence=occurrence,
            )
        except ProblemError as exc:
            if exc.code != "billing.entry_conflict":
                raise
            log.warning("earning_conflict", partner_id=str(partner.id), order_id=str(order_id))
            return 0
    return amount


async def _mover(
    session: AsyncSession,
    order_id: uuid.UUID,
    to_status: str,
    kind: str,
    event_id: uuid.UUID | None,
) -> tuple[Partner, datetime] | None:
    """The partner record of the person who made this very move, and when; None for a hub, the
    system or a person with no record of that kind."""
    move = await orders_api.last_move(session, order_id, to_status, event_id)
    if move is None or move.actor_user_id is None:
        return None
    partner = await session.scalar(
        select(Partner).where(Partner.user_id == move.actor_user_id, Partner.kind == kind)
    )
    return None if partner is None else (partner, move.at)


async def _holder(session: AsyncSession, order_id: uuid.UUID) -> tuple[uuid.UUID, int] | None:
    """The kitchen whose assignment holds the order, and the order's portions there."""
    row = (
        await session.execute(
            select(Assignment.partner_id, AssignmentOrder.portions)
            .join(AssignmentOrder, AssignmentOrder.assignment_id == Assignment.id)
            .where(AssignmentOrder.order_id == order_id, AssignmentOrder.status != "released")
        )
    ).first()
    return None if row is None else (row[0], row[1])


async def on_order_event(session: AsyncSession, event: OutboxEvent) -> None:
    """`orders.delivered`, `orders.dispatched` and `orders.packed`: post what they earned. Each
    trip is paid once, keyed by the timeline row of the move (`order_event_id`), so a box
    dispatched again for its free redelivery pays its second rider too, and a replay pays
    nothing."""
    payload = event.payload
    try:
        order_id = uuid.UUID(str(payload["order_id"]))
        hub_id = uuid.UUID(str(payload["hub_id"]))
        on = date.fromisoformat(str(payload["delivery_date"]))
        raw = payload.get("order_event_id")
        occurrence = None if raw is None else uuid.UUID(str(raw))
    except (KeyError, ValueError):
        return  # not an order event of M4's shape: nothing to pay, and nothing to retry
    kind, actor = payload.get("kind"), payload.get("actor")
    status = event.event_type.split(".")[1]  # delivered, dispatched or packed: the move's target
    work: dict[str, Any] = {"order_id": order_id, "hub_id": hub_id, "on": on}
    if event.event_type == orders_api.DELIVERED:
        if kind == "tiffin":
            held = await _holder(session, order_id)
            cook = None if held is None else await session.get(Partner, held[0])
            move = await orders_api.last_move(session, order_id, status, occurrence)
            if cook is not None and held is not None and move is not None:
                await pay(
                    session, cook, units=held[1], earned_at=move.at, occurrence=occurrence, **work
                )
        elif kind in ("box", "addon") and actor == "captain":
            found = await _mover(session, order_id, status, "captain", occurrence)
            if found is not None:
                await pay(
                    session, found[0], units=1, earned_at=found[1], occurrence=occurrence, **work
                )
        return
    field = FIELD.get(event.event_type)
    if field is not None and actor == field[1]:
        found = await _mover(session, order_id, status, field[0], occurrence)
        if found is not None:
            await pay(session, found[0], units=1, earned_at=found[1], occurrence=occurrence, **work)


def parse_week(value: str | None) -> str:
    """An ISO week as `YYYY-Www`, no later than the current one (IST); default this week."""
    this = kitchens.today().isocalendar()
    if value is None:
        return f"{this.year}-W{this.week:02d}"
    invalid = ProblemError(
        422, "partners.week_invalid", "The week is YYYY-Www, no later than the current week."
    )
    match = WEEK.match(value)
    if match is None:
        raise invalid
    try:
        monday = date.fromisocalendar(int(match[1]), int(match[2]), 1)
    except ValueError as exc:
        raise invalid from exc
    if monday > kitchens.today():
        raise invalid
    return value
