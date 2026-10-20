"""Test helpers for tiffin assignments: locked tiffin orders made through M4's facade, and
kitchens near the seed societies."""

import uuid
from datetime import UTC, date, datetime, time

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.billing.wallet import credit_topup
from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.catalog.models import Product
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.identity.models import User, UserRole
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.orders import service as order_service
from gharbasket.modules.orders.models import Order
from gharbasket.modules.partners import kitchens, service
from gharbasket.modules.partners.models import Kitchen
from gharbasket.platform.geo import point
from gharbasket.tools.seed import SEED_HUB_ID

T = date(2027, 1, 15)  # a Friday
EVENING = datetime(2027, 1, 14, 16, 0, tzinfo=UTC)  # T-1 21:30 IST
SUNRISE = (19.0395, 73.0675)  # Sunrise Towers (Priya, Meena)
KRISHNA = (19.0005, 73.1185)  # Krishna Kunj (Ramesh), about 8 km from Sunrise
FAR = (19.2000, 73.2000)  # about 25 km away


async def user_id(db: AsyncSession, phone: str) -> uuid.UUID:
    found = await db.scalar(select(User.id).where(User.phone_e164 == phone))
    assert found is not None
    return found


async def tiffin_order(
    db: AsyncSession,
    phone: str,
    portions: int,
    *,
    slug: str = "tomato",
    start: time = time(12, 30),
    day: date = T,
) -> uuid.UUID:
    """A tiffin of `portions` steps of `slug`, paid from the wallet and locked; committed."""
    owner = await user_id(db, phone)
    household = await identity_api.household_for_user(db, owner)
    assert household is not None
    slot = await order_service.create_slot(
        db,
        hub_id=SEED_HUB_ID,
        label=f"Tiffin {uuid.uuid4().hex[:6]}",
        kind="box",
        start=start,
        end=time(start.hour + 1, start.minute),
        capacity=50,
    )
    product = (await catalog_api.product_ids_by_slug(db, [slug]))[slug]
    order = await orders_api.create_from_cycle(
        db,
        orders_api.ApprovedCycle(
            cycle_id=uuid.uuid4(),
            household_id=household.id,
            hub_id=SEED_HUB_ID,
            society_id=household.society_id,
            delivery_date=day,
            slot_id=slot.id,
            lines=(orders_api.CycleLine(product, portions, 6_000),),
        ),
    )
    await credit_topup(
        db, owner, order.total_paise, payment_ref=uuid.uuid4().hex, topup_id=uuid.uuid4()
    )
    await billing_api.debit_for_order(db, owner, order.id, order.total_paise, day)
    await db.execute(update(Order).where(Order.id == order.id).values(kind="tiffin"))
    await orders_api.transition(db, order.id, "locked", actor="system")
    order_id = order.id
    await db.commit()
    return order_id


async def tag(db: AsyncSession, slug: str, *tags: str) -> None:
    await db.execute(update(Product).where(Product.slug == slug).values(tags=list(tags)))
    await db.commit()


async def kitchen(
    db: AsyncSession,
    phone: str,
    name: str,
    *,
    at: tuple[float, float] = SUNRISE,
    lunch: int = 10,
    skills: tuple[str, ...] = ("veg",),
) -> uuid.UUID:
    """A verified kitchen of the seed hub with the kitchen_partner role; committed."""
    owner = await user_id(db, phone)
    partner = await service.verify(
        db, user_id=owner, kind="kitchen", hub_id=SEED_HUB_ID, display_name=name
    )
    made = await kitchens.create_for(db, partner)
    made.location, made.skills = point(*at), list(skills)
    made.default_portions = {"lunch": [lunch] * 7, "dinner": [0] * 7}
    has_role = await db.scalar(
        select(UserRole.id).where(UserRole.user_id == owner, UserRole.role == "kitchen_partner")
    )
    if has_role is None:
        db.add(
            UserRole(
                user_id=owner,
                role="kitchen_partner",
                scope_type="hub",
                scope_id=SEED_HUB_ID,
                status="verified",
            )
        )
    partner_id = partner.id
    await db.commit()
    return partner_id


async def kitchen_row(db: AsyncSession, partner_id: uuid.UUID) -> Kitchen:
    found = await db.get(Kitchen, partner_id)
    assert found is not None
    return found
