"""The console's product reads (spec §3.3): every product, active or not, with its synonyms, the
recipes using it and who last edited it, and the categories."""

import uuid
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog.models import (
    NUTRITION_SOURCES,
    Category,
    Product,
    Recipe,
    RecipeIngredient,
    SearchTerm,
)
from gharbasket.modules.catalog.service import ProductRow, page_by_name, product_not_found

NUTRITION_FIELDS = ("kcal", "protein_g", "fat_g", "carb_g", "fibre_g", "unit_g")


def nutrition_view(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    """The §3.3 fields of a product's nutrition (rows seeded before §3.3 hold a source object)."""
    if not raw:
        return None
    source = raw.get("source")
    if isinstance(source, dict):
        source = NUTRITION_SOURCES.get(str(source.get("name")), "manual")  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
    return {**{key: raw[key] for key in NUTRITION_FIELDS if key in raw}, "source": source}


async def categories(session: AsyncSession) -> list[Category]:
    rows = await session.execute(select(Category).order_by(Category.sort_order, Category.slug))
    return list(rows.scalars())


async def list_products(
    session: AsyncSession,
    *,
    q: str | None,
    kind: str | None,
    category: str | None,
    active: bool | None,
    cursor: str | None,
    limit: int,
) -> tuple[list[ProductRow], str | None]:
    stmt = select(Product, Category).join(Category, Category.id == Product.category_id)
    if q:
        names = (Product.names[lang].astext for lang in ("en", "hi", "mr"))
        stmt = stmt.where(
            or_(*(column.icontains(q, autoescape=True) for column in (Product.slug, *names)))
        )
    if kind:
        stmt = stmt.where(Product.kind == kind)
    if category:
        stmt = stmt.where(Category.slug == category)
    if active is not None:
        stmt = stmt.where(Product.active == active)
    return await page_by_name(session, stmt, cursor, limit)


async def get(session: AsyncSession, product_id: uuid.UUID) -> ProductRow:
    """A product whether active or not (the console sees both)."""
    stmt = (
        select(Product, Category)
        .join(Category, Category.id == Product.category_id)
        .where(Product.id == product_id)
    )
    row = (await session.execute(stmt)).first()
    if row is None:
        raise product_not_found()
    return ProductRow(row[0], row[1])


async def synonyms(
    session: AsyncSession, product_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[dict[str, str]]]:
    found: dict[uuid.UUID, list[dict[str, str]]] = {product_id: [] for product_id in product_ids}
    rows = await session.execute(
        select(SearchTerm.product_id, SearchTerm.term, SearchTerm.lang)
        .where(SearchTerm.product_id.in_(product_ids), SearchTerm.source == "synonym")
        .order_by(SearchTerm.id)
    )
    for product_id, term, lang in rows.all():
        found[product_id].append({"term": term, "lang": lang})
    return found


async def used_by_recipes(
    session: AsyncSession, product_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    rows = await session.execute(
        select(RecipeIngredient.product_id, func.count())
        .join(Recipe, Recipe.id == RecipeIngredient.recipe_id)
        .where(RecipeIngredient.product_id.in_(product_ids), Recipe.active)
        .group_by(RecipeIngredient.product_id)
    )
    return {product_id: count for product_id, count in rows.all()}
