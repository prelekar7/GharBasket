"""Delivery attempts (spec §3.4, §4): attempt 1 opens when the order's route starts, with the
household's delivery code; later steps move the latest attempt."""

import secrets
import uuid
from datetime import date

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.logistics.models import (
    Delivery,
    Route,
    RoutePlan,
    RouteStop,
    booked,
)
from gharbasket.modules.logistics.schemas import DeliveryOut
from gharbasket.modules.logistics.views import hhmm, now
from gharbasket.modules.orders import api as orders_api


def new_code() -> str:
    return f"{secrets.randbelow(10_000):04d}"


async def open_attempts(
    session: AsyncSession, stop: RouteStop, orders: list[orders_api.Order]
) -> None:
    """Attempt 1 of each order dispatched on the stop (route start)."""
    for order in orders:
        session.add(
            Delivery(
                order_id=order.id,
                attempt=1,
                hub_id=order.hub_id,
                society_id=order.society_id,
                order_kind=order.kind,
                delivery_date=order.delivery_date,
                slot_id=order.slot_id,
                stop_id=stop.id,
                status="on_route",
                delivery_code=new_code(),
            )
        )
    await session.flush()


async def latest(
    session: AsyncSession, order_id: uuid.UUID, *, for_update: bool = False
) -> Delivery | None:
    stmt = (
        select(Delivery)
        .where(Delivery.order_id == order_id)
        .order_by(Delivery.attempt.desc())
        .limit(1)
    )
    if for_update:  # read again after the lock, so a move made while we waited is seen
        stmt = stmt.with_for_update().execution_options(populate_existing=True)
    return (await session.execute(stmt)).scalar_one_or_none()


async def set_status(
    session: AsyncSession, order_ids: list[uuid.UUID], status: str, **values: object
) -> None:
    """The latest attempt of each order moves to `status` (handover, route complete)."""
    for order_id in order_ids:
        found = await latest(session, order_id)
        if found is not None:
            await session.execute(
                update(Delivery)
                .where(Delivery.id == found.id)
                .values(status=status, at=now(), **values)
                .execution_options(synchronize_session="fetch")
            )


async def orphaned(session: AsyncSession, delivery: Delivery, today: date) -> bool:
    """A redelivery or left-behind ride still `scheduled` that no van will take: its day has passed,
    or its day's plan is published without a stop for its society and slot (it was solved first).
    Only the hub can resolve it (M3 review)."""
    if delivery.status != "scheduled" or not (delivery.attempt == 2 or delivery.hub_ride):
        return False
    if delivery.delivery_date < today:
        return True
    plan = await session.scalar(
        select(RoutePlan.id).where(
            RoutePlan.hub_id == delivery.hub_id,
            RoutePlan.delivery_date == delivery.delivery_date,
            RoutePlan.kind == "box",
            RoutePlan.status == "published",
        )
    )
    if plan is None:
        return False
    stop = await session.scalar(
        select(RouteStop.id)
        .join(Route, Route.id == RouteStop.route_id)
        .where(
            Route.plan_id == plan,
            Route.status != "cancelled",
            RouteStop.society_id == delivery.society_id,
            RouteStop.slot_id == delivery.slot_id,
        )
        .limit(1)
    )
    return stop is None


def delivery_out(row: Delivery) -> DeliveryOut:
    return DeliveryOut(
        order_id=row.order_id,
        attempt=row.attempt,
        delivery_date=row.delivery_date,
        society_id=row.society_id,
        status=row.status,  # pyright: ignore[reportArgumentType]
        reason=row.reason,  # pyright: ignore[reportArgumentType]
        hold_until=None if row.hold_until is None else hhmm(row.hold_until),
        proof=row.proof,  # pyright: ignore[reportArgumentType]
        by=row.by_actor,  # pyright: ignore[reportArgumentType]
        at=row.at,
    )


async def redeliveries_for(
    session: AsyncSession, hub_id: uuid.UUID, day: date, status: str = "scheduled"
) -> list[Delivery]:
    """The hub's redeliveries (attempt 2) on `day` in `status`: T+1's solve and route start."""
    rows = await session.execute(
        select(Delivery)
        .where(
            Delivery.hub_id == hub_id,
            Delivery.delivery_date == day,
            booked(),
            Delivery.status == status,
        )
        .order_by(Delivery.id)
    )
    return list(rows.scalars())


async def riding(session: AsyncSession, stop_ids: list[uuid.UUID]) -> list[Delivery]:
    """The live redeliveries booked on these stops (a refunded one rides no more)."""
    if not stop_ids:
        return []
    rows = await session.execute(
        select(Delivery).where(
            Delivery.stop_id.in_(stop_ids), booked(), Delivery.status != "refunded"
        )
    )
    return list(rows.scalars())


async def redeliveries_on(
    session: AsyncSession, society_id: uuid.UUID, day: date, slot_id: uuid.UUID | None = None
) -> list[Delivery]:
    """A society's redeliveries (attempt 2) booked on `day`, one slot's when given."""
    stmt = select(Delivery).where(
        Delivery.society_id == society_id, Delivery.delivery_date == day, booked()
    )
    if slot_id is not None:
        stmt = stmt.where(Delivery.slot_id == slot_id)
    return list((await session.execute(stmt.order_by(Delivery.id))).scalars())
