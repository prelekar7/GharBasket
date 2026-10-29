"""Tiffin pickups for M7's tiffin routes (spec "Facade"): the kitchens with food to collect, and
the rider's scan of a kitchen's `pickup_qr` (`GB1A:` + the assignment id without hyphens).

M7 reads these through `partners.api`; it moves the orders `packed → dispatched` itself.
"""

import uuid
from dataclasses import dataclass
from datetime import date, time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.partners import assignments
from gharbasket.modules.partners.models import Assignment, AssignmentOrder, Kitchen, Partner
from gharbasket.platform.errors import ProblemError

QR_PREFIX = "GB1A:"
# An assignment whose kitchen accepted it and whose orders are not all collected.
COLLECTABLE = ("accepted", "preparing", "ready")


@dataclass(frozen=True)
class PickupOrder:
    order_id: uuid.UUID
    short_code: str  # "T-07", the label on the tiffin
    portions: int


@dataclass(frozen=True)
class PickupRef:
    assignment_id: uuid.UUID
    partner_id: uuid.UUID
    display_name: str
    address: str | None
    lat: float | None
    lng: float | None
    ready_by: time | None
    status: str  # the assignment's
    order_ids: tuple[uuid.UUID, ...]  # what is there to collect
    slot_id: uuid.UUID  # the assignment's (its earliest order's) slot
    orders: tuple[PickupOrder, ...]  # the same orders, with their labels and portions


@dataclass(frozen=True)
class KitchenPlace:
    """Where a kitchen is, for M7's kitchen stops: `area` is public, the address and the point
    are for the route's rider at pickup and for hub staff (M7 decides who sees them)."""

    partner_id: uuid.UUID
    display_name: str
    area: str | None
    address: str | None
    lat: float | None
    lng: float | None


def pickup_qr(assignment_id: uuid.UUID) -> str:
    return f"{QR_PREFIX}{assignment_id.hex}"


def invalid() -> ProblemError:
    return ProblemError(404, "partners.pickup_invalid", "This pickup code is not valid here.")


async def _refs(
    session: AsyncSession, found: list[Assignment], order_status: tuple[str, ...]
) -> list[PickupRef]:
    refs = []
    for assignment in found:
        partner = await session.get(Partner, assignment.partner_id)
        kitchen = await session.get(Kitchen, assignment.partner_id)
        if partner is None or kitchen is None:
            continue
        rows = await session.execute(
            select(AssignmentOrder)
            .where(
                AssignmentOrder.assignment_id == assignment.id,
                AssignmentOrder.status.in_(order_status),
            )
            .order_by(AssignmentOrder.short_code)
        )
        held = [PickupOrder(r.order_id, r.short_code, r.portions) for r in rows.scalars()]
        slot = await orders_api.slot_ref(session, assignment.slot_id)
        refs.append(
            PickupRef(
                assignment_id=assignment.id,
                partner_id=partner.id,
                display_name=partner.display_name,
                address=kitchen.address,
                lat=kitchen.lat,
                lng=kitchen.lng,
                ready_by=assignments.ready_by(slot.start) if slot else None,
                status=assignment.status,
                order_ids=tuple(o.order_id for o in held),
                slot_id=assignment.slot_id,
                orders=tuple(held),
            )
        )
    return refs


async def kitchen_places(
    session: AsyncSession, partner_ids: list[uuid.UUID]
) -> dict[uuid.UUID, KitchenPlace]:
    """The kitchens of these partners; unknown ids and partners without a kitchen are left out."""
    if not partner_ids:
        return {}
    rows = await session.execute(
        select(Partner, Kitchen)
        .join(Kitchen, Kitchen.partner_id == Partner.id)
        .where(Partner.id.in_(set(partner_ids)))
    )
    return {
        partner.id: KitchenPlace(
            partner.id,
            partner.display_name,
            kitchen.area,
            kitchen.address,
            kitchen.lat,
            kitchen.lng,
        )
        for partner, kitchen in rows.all()
    }


async def tiffin_pickups(
    session: AsyncSession, hub_id: uuid.UUID, on_date: date, meal: str
) -> list[PickupRef]:
    """The hub's kitchens with tiffins to collect for a date and meal, by ready-by time: every
    order not yet picked up (cooking or ready), so routes can be planned before the food is."""
    found = (
        (
            await session.execute(
                select(Assignment)
                .where(
                    Assignment.hub_id == hub_id,
                    Assignment.on_date == on_date,
                    Assignment.meal == meal,
                    Assignment.status.in_(COLLECTABLE),
                )
                .order_by(Assignment.id)
            )
        )
        .scalars()
        .all()
    )
    refs = await _refs(session, list(found), ("assigned", "ready"))
    return sorted(refs, key=lambda r: (r.ready_by or time.max, str(r.assignment_id)))


async def pickup(session: AsyncSession, code: str, hub_id: uuid.UUID) -> PickupRef:
    """The orders ready at the kitchen whose QR the rider scanned. 404 `partners.pickup_invalid`
    for a code that is malformed, unknown, or another hub's."""
    if not code.startswith(QR_PREFIX):
        raise invalid()
    try:
        assignment_id = uuid.UUID(hex=code.removeprefix(QR_PREFIX))
    except ValueError as exc:
        raise invalid() from exc
    assignment = await session.get(Assignment, assignment_id)
    if assignment is None or assignment.hub_id != hub_id:
        raise invalid()
    refs = await _refs(session, [assignment], ("ready",))
    if not refs:
        raise invalid()
    return refs[0]
