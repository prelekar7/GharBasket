"""A lunch tiffin day for M7's tiffin routes (§3.7): locked tiffins in one lunch slot, kitchens
near the seed societies holding them in accepted M8 assignments, and two-wheelers."""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from itertools import count

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.billing.wallet import credit_topup
from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.orders import service as order_service
from gharbasket.modules.orders.models import Order
from gharbasket.modules.partners import cooking
from gharbasket.modules.partners.models import Assignment, AssignmentOrder
from gharbasket.tools.seed import SEED_HUB_ID

from ..partners.tiffins import kitchen
from .conftest import user_id

T = date(2026, 10, 13)  # a Tuesday, the box tests' DAY
SOLVE_AT = datetime(2026, 10, 13, 4, 5, tzinfo=UTC)  # 09:35 IST
START_AT = datetime(2026, 10, 13, 5, 30, tzinfo=UTC)  # 11:00 IST
COLLECT_AT = datetime(2026, 10, 13, 6, 15, tzinfo=UTC)  # 11:45 IST
DOORS_AT = datetime(2026, 10, 13, 7, 15, tzinfo=UTC)  # 12:45 IST
CLOSE_AT = datetime(2026, 10, 13, 10, 30, tzinfo=UTC)  # 16:00 IST
LATA = "+919800001005"  # kitchen partner role at the seed hub
ASHA = "+919800001010"
REKHA = "+919800001013"
SUNRISE = (19.0395, 73.0675)  # Sunrise Towers (Priya, Meena)
KRISHNA = (19.0005, 73.1185)  # Krishna Kunj (Ramesh, Savita)
CHECKLIST: dict[str, bool] = dict.fromkeys(
    ("handwash", "hair_cover", "clean_surface", "sealed", "fresh_today"), True
)
_codes = count(1)


async def lunch_slot(db: AsyncSession, start: time = time(12, 30)) -> uuid.UUID:
    made = await order_service.create_slot(
        db,
        hub_id=SEED_HUB_ID,
        label=f"Lunch {uuid.uuid4().hex[:6]}",
        kind="tiffin",
        meal="lunch",
        start=start,
        end=time(start.hour + 1, start.minute),
        capacity=50,
    )
    return made.id


async def tiffin(db: AsyncSession, phone: str, slot_id: uuid.UUID, portions: int) -> uuid.UUID:
    """A tiffin of `portions` paid from the owner's wallet and locked; committed."""
    owner = await user_id(db, phone)
    household = await identity_api.household_for_user(db, owner)
    assert household is not None
    product = (await catalog_api.product_ids_by_slug(db, ["tomato"]))["tomato"]
    order = await orders_api.create_from_cycle(
        db,
        orders_api.ApprovedCycle(
            cycle_id=uuid.uuid4(),
            household_id=household.id,
            hub_id=SEED_HUB_ID,
            society_id=household.society_id,
            delivery_date=T,
            slot_id=slot_id,
            lines=(orders_api.CycleLine(product, portions, 6_000),),
        ),
    )
    await credit_topup(
        db, owner, order.total_paise, payment_ref=uuid.uuid4().hex, topup_id=uuid.uuid4()
    )
    await billing_api.debit_for_order(db, owner, order.id, order.total_paise, T)
    await db.execute(update(Order).where(Order.id == order.id).values(kind="tiffin"))
    await orders_api.transition(db, order.id, "locked", actor="system")
    order_id = order.id
    await db.commit()
    return order_id


async def assign(
    db: AsyncSession,
    partner_id: uuid.UUID,
    slot_id: uuid.UUID,
    orders: dict[uuid.UUID, int],
    *,
    status: str = "accepted",
) -> uuid.UUID:
    """An M8 assignment of these orders (order -> portions) to the kitchen; committed."""
    made = Assignment(
        partner_id=partner_id,
        hub_id=SEED_HUB_ID,
        on_date=T,
        meal="lunch",
        slot_id=slot_id,
        round=1,
        source="manual",
        status=status,
        portions=sum(orders.values()),
        respond_by=SOLVE_AT - timedelta(hours=12),
    )
    db.add(made)
    await db.flush()
    for order_id, portions in orders.items():
        db.add(
            AssignmentOrder(
                assignment_id=made.id,
                order_id=order_id,
                portions=portions,
                short_code=f"T-{next(_codes):02d}",
            )
        )
    assignment_id = made.id
    await db.commit()
    return assignment_id


async def ready(
    db: AsyncSession,
    assignment_id: uuid.UUID,
    phone: str,
    order_ids: list[uuid.UUID] | None = None,
) -> None:
    """The kitchen taps Ready (M8): the orders go `packed` and their rows `ready`; committed."""
    found = await db.get(Assignment, assignment_id, populate_existing=True)
    assert found is not None
    await cooking.ready(
        db,
        found,
        await user_id(db, phone),
        order_ids=order_ids,
        checklist=CHECKLIST,
        at=START_AT,
        client_at=None,
    )
    await db.commit()


def qr(assignment_id: uuid.UUID) -> str:
    return f"GB1A:{assignment_id.hex}"


__all__ = ["kitchen"]
