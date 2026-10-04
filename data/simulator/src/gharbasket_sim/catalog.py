"""What the simulator needs from M2 Catalog, read through the catalog services.

The recipes come out in the shape of the planner's request (M10 §3.1), sorted by slug so the
same database always gives the same world.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog import recipes as catalog_recipes
from gharbasket.modules.catalog import service as catalog_service
from gharbasket.modules.catalog.recipes import BASE_UNIT

# Bought for at most this many hours of shelf life: the planner keeps these few (less waste).
PERISHABLE_H = 96
PAGE = 200


@dataclass(frozen=True)
class Product:
    slug: str
    unit: str  # base unit: g, ml, piece or bunch
    kind: str  # veg, fruit, staple
    tags: frozenset[str]


@dataclass(frozen=True, order=True)
class Ingredient:
    product: str
    qty: float  # base units per serving
    optional: bool


@dataclass(frozen=True)
class Festival:
    day: date
    code: str
    rules: dict[str, bool]


@dataclass(frozen=True)
class Catalog:
    products: dict[str, Product]
    recipes: list[dict[str, Any]]  # PlanRecipe bodies
    ingredients: dict[str, tuple[Ingredient, ...]]
    festivals: list[Festival]

    def fruits(self) -> list[str]:
        return sorted(slug for slug, p in self.products.items() if p.kind == "fruit")


def is_side(recipe: Any) -> bool:
    """Chapati is eaten with a sabzi or dal, not planned as a meal (M2 §3.2)."""
    return bool(getattr(recipe, "is_side", False))


async def load_catalog(session: AsyncSession, hub_id: Any, start: date, end: date) -> Catalog:
    products: dict[str, Product] = {}
    cursor = None
    while True:
        rows, cursor = await catalog_service.list_products(
            session, kind=None, category=None, tag=None, cursor=cursor, limit=PAGE
        )
        for row in rows:
            p = row.product
            products[p.slug] = Product(p.slug, BASE_UNIT[p.step_unit][0], p.kind, frozenset(p.tags))
        if cursor is None:
            break

    found = []
    cursor = None
    while True:
        rows, cursor = await catalog_recipes.list_recipes(
            session,
            cuisine=None,
            slot=None,
            diet=None,
            jain_ok=None,
            fasting_ok=None,
            cursor=cursor,
            limit=PAGE,
        )
        found.extend(rows)
        if cursor is None:
            break

    recipes: list[dict[str, Any]] = []
    ingredients: dict[str, tuple[Ingredient, ...]] = {}
    for recipe in sorted(found, key=lambda r: r.slug):
        if is_side(recipe):
            continue
        _, items = await catalog_recipes.get_recipe(session, recipe.id)
        cost = await catalog_recipes.cost_per_serving(session, items, hub_id)
        if cost is None:
            raise LookupError(f"{recipe.slug} has an ingredient without a price at the hub")
        recipes.append(
            {
                "slug": recipe.slug,
                "cuisine": recipe.cuisine,
                "slots": list(recipe.slots),
                "cook_minutes": recipe.cook_minutes,
                "diet": recipe.diet,
                "jain_ok": recipe.rules["jain_ok"],
                "no_onion_garlic": recipe.rules["no_onion_garlic"],
                "fasting_ok": recipe.rules["fasting_ok"],
                "diabetic_ok": recipe.rules["diabetic_ok"],
                "no_cook": "no_cook" in recipe.tags,
                "cost_per_serving_paise": cost,
                "perishables": sorted(
                    i.product.slug
                    for i in items
                    if i.product.shelf_life_h is not None and i.product.shelf_life_h <= PERISHABLE_H
                ),
            }
        )
        ingredients[recipe.slug] = tuple(
            sorted(Ingredient(i.product.slug, float(i.qty), i.optional) for i in items)
        )

    days = await catalog_recipes.festivals(session, start, end, None)
    return Catalog(
        products,
        recipes,
        ingredients,
        [Festival(f.day, f.code, dict(f.rules)) for f in days],
    )
