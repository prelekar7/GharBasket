"""Direct tiffin orders (spec §3.5): a household books a kitchen's menu for one meal.

The order is `approved` with one line (the menu's tiffin product x portions at the menu price) in
the hub's tiffin slot for that meal, and is paid at the 21:00 cut-off like a box. The menu's
kitchen is copied to the order as `preferred_partner_id`, a preference M8's assignment honours
while that kitchen can take the order (M8 §3.4). One tiffin per household, date and meal.
"""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.orders import events, service
from gharbasket.modules.orders.models import Order, OrderEvent, OrderLine, Slot
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.locale import Language, localised
from gharbasket.platform.outbox import publish

MAX_PORTIONS = 12
MENU_DAYS = 7  # tomorrow … tomorrow + 6
MAX_CHOICES = 14  # dates and meals offered to the assistant (7 days x 2 meals)
MAX_MENUS = 5  # menus of one date and meal offered to the assistant


def bookable_window() -> tuple[date, date]:
    tomorrow = service.now().astimezone(service.IST).date() + timedelta(days=1)
    return tomorrow, tomorrow + timedelta(days=MENU_DAYS - 1)


async def default_portions(session: AsyncSession, household_id: uuid.UUID) -> int:
    """What a booking gets when the person does not say: the household size, at most 12."""
    profile = await identity_api.household_plan_profile(session, household_id)
    return min(profile.size if profile else 1, MAX_PORTIONS)


@dataclass(frozen=True)
class MenuChoice:
    id: uuid.UUID
    dishes: tuple[str, ...]  # in the person's language
    price_paise: int  # per portion


@dataclass(frozen=True)
class TiffinChoice:
    """One date and meal a household may book: its open menus, or `booked` (then no menus)."""

    on_date: date
    meal: str
    order_by: datetime
    booked: bool
    menus: tuple[MenuChoice, ...]


async def choices(
    session: AsyncSession, household_id: uuid.UUID, hub_id: uuid.UUID, lang: Language
) -> list[TiffinChoice]:
    """The menus `GET /v1/tiffin-menus` lists (approved, before their `order_by`, tomorrow …
    tomorrow + 6) grouped by date and meal, with `booked` where the household already has a
    tiffin that is not cancelled. No kitchen or partner is named."""
    first, last = bookable_window()
    menus = await catalog_api.tiffin_menus(session, hub_id, first, last)
    taken: set[tuple[date, str]] = {
        (day, meal)
        for day, meal in (
            await session.execute(
                select(Order.delivery_date, Slot.meal)
                .join(Slot, Slot.id == Order.slot_id)
                .where(
                    Order.household_id == household_id,
                    Order.kind == "tiffin",
                    Order.status != "cancelled",
                    Order.delivery_date.between(first, last),
                )
            )
        ).all()
        if meal is not None
    }
    grouped: dict[tuple[date, str], list[MenuChoice]] = {}
    for menu in menus:
        if service.now() < service.cutoff_for(menu.on_date):
            names = tuple(localised(n, lang) for _, n in menu.dishes)
            grouped.setdefault((menu.on_date, menu.meal), []).append(
                MenuChoice(menu.id, names, menu.price_paise)
            )
    for key in taken:  # a booked meal is listed (without menus) while it could still be booked
        if service.now() < service.cutoff_for(key[0]):
            grouped.setdefault(key, [])
    return [
        TiffinChoice(
            on_date,
            meal,
            service.cutoff_for(on_date),
            (on_date, meal) in taken,
            () if (on_date, meal) in taken else tuple(found[:MAX_MENUS]),
        )
        for (on_date, meal), found in sorted(
            grouped.items(), key=lambda g: (g[0][0], g[0][1] != "lunch")
        )
    ][:MAX_CHOICES]


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
        portions = await default_portions(session, household.id)
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
