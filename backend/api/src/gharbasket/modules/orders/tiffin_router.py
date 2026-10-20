"""HTTP endpoints of M4 for tiffins (spec §3.5): the menus a household may book. The menus are M2's
data (`catalog.tiffin_menus`); the contract is M4's because only this flow reads it."""

from datetime import date, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Query, Response

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.orders import service
from gharbasket.modules.orders.schemas import (
    DishOut,
    Meal,
    MenuKitchenOut,
    TiffinMenuOut,
    TiffinMenusOut,
)
from gharbasket.modules.partners import api as partners_api
from gharbasket.platform.auth import CurrentPrincipal
from gharbasket.platform.db import DbSession
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.locale import Lang, Language, localised

router = APIRouter(tags=["orders"])
MENU_DAYS = 7  # tomorrow … tomorrow + 6


def _problem(description: str) -> dict[str, Any]:
    return {"description": description, "content": {"application/problem+json": {}}}


def bookable_window() -> tuple[date, date]:
    tomorrow = service.now().astimezone(service.IST).date() + timedelta(days=1)
    return tomorrow, tomorrow + timedelta(days=MENU_DAYS - 1)


async def menu_out(
    session: DbSession, menu: catalog_api.TiffinMenuRef, lang: Language
) -> TiffinMenuOut:
    card = await partners_api.profile_card(session, menu.partner_id)
    return TiffinMenuOut(
        id=menu.id,
        date=menu.on_date,
        meal=menu.meal,  # pyright: ignore[reportArgumentType]
        hub_id=menu.hub_id,
        dishes=[DishOut(recipe_id=rid, name=localised(names, lang)) for rid, names in menu.dishes],
        price_paise=menu.price_paise,
        kitchen=MenuKitchenOut(
            partner_id=menu.partner_id,
            display_name=card.display_name if card else "",
            photo_url=None,
        ),
        order_by=service.cutoff_for(menu.on_date),
    )


@router.get(
    "/tiffin-menus",
    summary="Approved tiffin menus of your hub, tomorrow to a week ahead, still open to book",
    responses={
        401: _problem("auth.invalid_token"),
        422: _problem("orders.hub_required, orders.date_out_of_range, request.invalid"),
    },
)
async def tiffin_menus(
    principal: CurrentPrincipal,
    session: DbSession,
    response: Response,
    lang: Lang,
    on: Annotated[date | None, Query(alias="date")] = None,
    meal: Meal | None = None,
) -> TiffinMenusOut:
    hub_id = await identity_api.household_hub(session, principal.user_id)
    if hub_id is None:
        raise ProblemError(422, "orders.hub_required", "Set up your household first.")
    first, last = bookable_window()
    if on is not None:
        if not first <= on <= last:
            raise ProblemError(
                422, "orders.date_out_of_range", "Tiffins are booked from tomorrow to a week ahead."
            )
        first = last = on
    menus = await catalog_api.tiffin_menus(session, hub_id, first, last, meal)
    response.headers["Cache-Control"] = "no-store"
    return TiffinMenusOut(
        items=[
            await menu_out(session, menu, lang)
            for menu in menus
            if service.now() < service.cutoff_for(menu.on_date)
        ]
    )
