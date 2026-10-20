"""Test helpers for tiffins: kitchens' menus (M2's `catalog.tiffin_menus`, written directly: M2
has no menu-writing API yet)."""

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.catalog.models import Recipe, TiffinMenu
from gharbasket.tools.seed import SEED_HUB_ID


async def menu(
    db: AsyncSession,
    on: date,
    *,
    meal: str = "lunch",
    partner_id: uuid.UUID | None = None,
    hub_id: uuid.UUID = SEED_HUB_ID,
    status: str = "approved",
    price_paise: int = 11_000,
    slug: str = "tomato",
) -> TiffinMenu:
    """A kitchen's menu with the first two seed recipes as its dishes; flushed."""
    recipes = (await db.execute(select(Recipe.id).order_by(Recipe.slug).limit(2))).scalars().all()
    product = (await catalog_api.product_ids_by_slug(db, [slug]))[slug]
    made = TiffinMenu(
        hub_id=hub_id,
        partner_id=partner_id or uuid.uuid4(),
        on_date=on,
        meal=meal,
        product_id=product,
        recipe_ids=list(recipes),
        price_paise=price_paise,
        status=status,
    )
    db.add(made)
    await db.flush()
    return made
