"""`just seed` and the console (M2 §3.3): the seed only adds missing products and recipes, and
`--refresh` re-applies the seed files to the rows nobody edited in the console."""

import uuid

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog.models import Price, Product, Recipe
from gharbasket.tools.seed import seed_catalog

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("catalog")]


async def _id(db: AsyncSession, slug: str) -> uuid.UUID:
    return (await db.execute(select(Product.id).where(Product.slug == slug))).scalar_one()


async def _product(db: AsyncSession, slug: str) -> Product:
    return (await db.execute(select(Product).where(Product.slug == slug))).scalar_one()


async def test_the_seed_adds_missing_rows_and_refreshes_only_unedited_ones_on_request(
    db_session: AsyncSession,
) -> None:
    mint = await _id(db_session, "mint")
    await db_session.execute(delete(Price).where(Price.product_id == mint))
    await db_session.execute(delete(Product).where(Product.id == mint))
    methi, okra = await _product(db_session, "methi"), await _product(db_session, "bhindi")
    seeded_names = dict(methi.names)
    methi.names = {**methi.names, "en": "Stale"}  # a seed-file fix not applied yet
    okra.names = {**okra.names, "en": "Okra"}
    okra.updated_by = uuid.uuid4()  # edited in the console
    thepla, bhaji = [
        (await db_session.execute(select(Recipe).where(Recipe.slug == slug))).scalar_one()
        for slug in ("methi-thepla", "methi-bhaji")
    ]
    cook_minutes = thepla.cook_minutes
    thepla.cook_minutes = cook_minutes + 5
    # Edited in the console, with derived rules that no longer match its onion and garlic.
    rules = dict(bhaji.rules)
    bhaji.rules, bhaji.cook_minutes = {**rules, "no_onion_garlic": True}, 99
    bhaji.updated_by = uuid.uuid4()
    await db_session.flush()

    added = dict(await seed_catalog(db_session))

    assert added["mint"] is True
    assert sum(added.values()) == 1
    await db_session.refresh(methi)
    await db_session.refresh(thepla)
    assert methi.names["en"] == "Stale" and thepla.cook_minutes == cook_minutes + 5

    await seed_catalog(db_session, refresh=True)

    for row in (methi, okra, thepla, bhaji):
        await db_session.refresh(row)
    assert methi.names == seeded_names and thepla.cook_minutes == cook_minutes
    assert okra.names["en"] == "Okra"  # the console's edits are kept
    assert bhaji.cook_minutes == 99 and bhaji.rules == rules  # but its rules follow the products
