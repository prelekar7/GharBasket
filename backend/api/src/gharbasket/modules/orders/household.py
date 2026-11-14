"""The household's current orders, for M13's `track_order` (spec M13 §3.1)."""

import uuid
from datetime import date, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.orders.models import Order
from gharbasket.modules.orders.state import FINAL

MAX_ORDERS = 10
OPEN_DAYS = 7  # an order not yet final still counts for this long after its delivery date


async def household_orders(
    session: AsyncSession, household_id: uuid.UUID, today: date
) -> list[Order]:
    """Orders that are not cancelled and either deliver today or later, or are not yet in a final
    status (a redelivery still to come) and are at most a week old; oldest delivery first."""
    rows = await session.execute(
        select(Order)
        .where(
            Order.household_id == household_id,
            Order.status != "cancelled",
            or_(
                Order.delivery_date >= today,
                (Order.delivery_date >= today - timedelta(days=OPEN_DAYS))
                & Order.status.not_in(FINAL),
            ),
        )
        .order_by(Order.delivery_date, Order.created_at, Order.id)
        .limit(MAX_ORDERS)
    )
    return list(rows.scalars())
