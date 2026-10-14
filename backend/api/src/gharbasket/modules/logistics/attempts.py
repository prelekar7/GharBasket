"""Delivery attempts (spec §3.4, §4): attempt 1 opens when the order's route starts, with the
household's delivery code; later steps move the latest attempt."""

import secrets
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.logistics.models import Delivery, RouteStop
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
    if for_update:
        stmt = stmt.with_for_update()
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
