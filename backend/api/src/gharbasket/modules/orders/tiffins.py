"""Direct tiffin orders (spec §3.5): a household books a kitchen's menu for one meal.

The order is `approved` with one line (the menu's tiffin product x portions at the menu price) in
the hub's tiffin slot for that meal, and is paid at the 21:00 cut-off like a box. The menu's
kitchen is copied to the order as `preferred_partner_id`, a preference M8's assignment honours
while that kitchen can take the order (M8 §3.4). One tiffin per household, date and meal.
"""

import hashlib
import uuid

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.orders import events, service
from gharbasket.modules.orders.models import Order, OrderEvent, OrderLine, Slot
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.outbox import publish

MAX_PORTIONS = 12


def _lock_key(household_id: uuid.UUID) -> int:
    digest = hashlib.sha256(f"gharbasket:tiffin:{household_id}".encode()).digest()
    return int.from_bytes(digest[:8], "big") >> 1


def menu_unknown(detail: str) -> ProblemError:
    return ProblemError(422, "orders.menu_unknown", detail)


async def tiffin_slot(session: AsyncSession, hub_id: uuid.UUID, meal: str) -> Slot:
    """The hub's active tiffin slot for the meal (the earliest when there are several)."""
    slot = (
        await session.execute(
            select(Slot)
            .where(Slot.hub_id == hub_id, Slot.kind == "tiffin", Slot.meal == meal, Slot.active)
            .order_by(Slot.start_time, Slot.id)
            .limit(1)
        )
    ).scalar_one_or_none()
    if slot is None:
        raise ProblemError(422, "orders.slot_invalid", f"No {meal} tiffin slot at this hub yet.")
    return slot


async def booked(
    session: AsyncSession, household_id: uuid.UUID, menu: catalog_api.TiffinMenuRef
) -> Order | None:
    """The household's tiffin for the menu's date and meal that is not cancelled."""
    return (
        await session.execute(
            select(Order)
            .join(Slot, Slot.id == Order.slot_id)
            .where(
                Order.household_id == household_id,
                Order.kind == "tiffin",
                Order.delivery_date == menu.on_date,
                Order.status != "cancelled",
                Slot.meal == menu.meal,
            )
        )
    ).scalar_one_or_none()


async def book(
    session: AsyncSession, user_id: uuid.UUID, menu_id: uuid.UUID, portions: int | None
) -> tuple[Order, catalog_api.TiffinMenuRef]:
    household = await identity_api.household_for_user(session, user_id)
    if household is None or household.hub_id is None:
        raise ProblemError(422, "orders.hub_required", "Set up your household first.")
    menu = await catalog_api.tiffin_menu(session, menu_id)
    if menu is None or menu.status != "approved" or menu.hub_id != household.hub_id:
        raise menu_unknown("This menu is not offered to your household.")
    if service.now() >= service.cutoff_for(menu.on_date):
        raise menu_unknown("Bookings for this menu closed at 21:00 the day before.")
    if portions is None:
        profile = await identity_api.household_plan_profile(session, household.id)
        portions = min(profile.size if profile else 1, MAX_PORTIONS)
    if not 1 <= portions <= MAX_PORTIONS:
        raise ProblemError(422, "orders.qty_invalid", "Book 1 to 12 portions.")
    product = (await catalog_api.line_products(session, [menu.product_id])).get(menu.product_id)
    if product is None:
        raise menu_unknown("This menu's tiffin is not on sale.")
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"), {"key": _lock_key(household.id)}
    )
    existing = await booked(session, household.id, menu)
    if existing is not None:
        raise ProblemError(
            409,
            "orders.tiffin_already_booked",
            "You already have a tiffin for this meal.",
            extra={"order_id": str(existing.id)},
        )
    slot = await tiffin_slot(session, household.hub_id, menu.meal)
    total = portions * menu.price_paise
    order = Order(
        household_id=household.id,
        hub_id=household.hub_id,
        society_id=household.society_id,
        kind="tiffin",
        delivery_date=menu.on_date,
        slot_id=slot.id,
        cutoff_at=service.cutoff_for(menu.on_date),
        status="approved",
        subtotal_paise=total,
        fees_paise=0,
        discount_paise=0,
        total_paise=total,
        tiffin_menu_id=menu.id,
        preferred_partner_id=menu.partner_id,
        portions=portions,
    )
    async with session.begin_nested():  # a full slot leaves no order behind
        session.add(order)
        await session.flush()
        session.add(
            OrderLine(
                order_id=order.id,
                product_id=menu.product_id,
                kind="box",
                qty=portions,
                step_qty=product.step_qty,
                step_unit=product.step_unit,
                unit_price_paise=menu.price_paise,
            )
        )
        await service.book_slot(session, slot.id, menu.on_date, order.id)
        session.add(
            OrderEvent(order_id=order.id, from_status=None, to_status="approved", actor="customer")
        )
        await session.flush()
    await publish(
        session,
        events.TIFFIN_BOOKED,
        {
            "order_id": str(order.id),
            "household_id": str(household.id),
            "delivery_date": menu.on_date.isoformat(),
            "meal": menu.meal,
            "portions": portions,
        },
    )
    await session.refresh(order)
    return order, menu
