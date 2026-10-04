"""Recipes and the festival calendar (spec §3.2): what the meal planner chooses from.

A recipe's `diet`, `jain_ok` and `no_onion_garlic` are calculated from its ingredients' product
tags every time it is saved, so they cannot drift from the ingredients. `fasting_ok` and
`diabetic_ok` are curated, because customs differ.
"""

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import delete, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog.models import Festival, Product, Recipe, RecipeIngredient
from gharbasket.modules.catalog.pricing import prices_for
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.pagination import decode_cursor, encode_cursor

# A product's step in the ingredient units: 1 kg is 1000 g, 1 l is 1000 ml.
BASE_UNIT = {
    "g": ("g", 1),
    "kg": ("g", 1000),
    "ml": ("ml", 1),
    "l": ("ml", 1000),
    "piece": ("piece", 1),
    "bunch": ("bunch", 1),
}


def recipe_not_found() -> ProblemError:
    return ProblemError(404, "catalog.recipe_not_found", "No such recipe.")


# --- writing (seed now; the console editor later) ---------------------------------------------


@dataclass(frozen=True)
class IngredientData:
    product: str  # product slug
    qty: Decimal
    unit: str
    optional: bool = False


@dataclass(frozen=True)
class RecipeData:
    slug: str
    names: dict[str, str]
    cuisine: str
    slots: tuple[str, ...]
    cook_minutes: int
    servings: int
    fasting_ok: bool
    diabetic_ok: bool
    ingredients: tuple[IngredientData, ...]
    tags: tuple[str, ...] = ()
    data_source: str = "real"


def derived_rules(ingredient_tags: list[set[str]]) -> tuple[str, bool, bool]:
    """(diet, jain_ok, no_onion_garlic) from the tags of a recipe's ingredients.

    Jain food has no onion, garlic or anything grown underground, and no eggs.
    """
    every: set[str] = set().union(*ingredient_tags) if ingredient_tags else set()
    diet = "egg" if "egg" in every else "veg"
    no_onion_garlic = "onion_garlic" not in every
    return diet, no_onion_garlic and "root" not in every and diet == "veg", no_onion_garlic


async def save_recipe(session: AsyncSession, data: RecipeData) -> tuple[Recipe, bool]:
    products = {
        product.slug: product
        for product in (
            await session.execute(
                select(Product).where(Product.slug.in_([i.product for i in data.ingredients]))
            )
        ).scalars()
    }
    if missing := sorted({i.product for i in data.ingredients} - set(products)):
        raise ValueError(f"recipe {data.slug}: unknown products {missing}")
    for ingredient in data.ingredients:
        product = products[ingredient.product]
        if BASE_UNIT[product.step_unit][0] != ingredient.unit:
            raise ValueError(
                f"recipe {data.slug}: {ingredient.product} is measured in "
                f"{BASE_UNIT[product.step_unit][0]}, not {ingredient.unit}"
            )
    diet, jain_ok, no_onion_garlic = derived_rules([set(p.tags) for p in products.values()])

    recipe = (
        await session.execute(select(Recipe).where(Recipe.slug == data.slug))
    ).scalar_one_or_none()
    created = recipe is None
    if recipe is None:
        recipe = Recipe(slug=data.slug)
        session.add(recipe)
    recipe.names = data.names
    recipe.cuisine = data.cuisine
    recipe.slots = list(data.slots)
    recipe.cook_minutes = data.cook_minutes
    recipe.servings = data.servings
    recipe.diet = diet
    recipe.rules = {
        "jain_ok": jain_ok,
        "no_onion_garlic": no_onion_garlic,
        "fasting_ok": data.fasting_ok,
        "diabetic_ok": data.diabetic_ok,
    }
    recipe.tags = list(data.tags)
    recipe.data_source = data.data_source
    await session.flush()
    await session.execute(delete(RecipeIngredient).where(RecipeIngredient.recipe_id == recipe.id))
    session.add_all(
        RecipeIngredient(
            recipe_id=recipe.id,
            product_id=products[i.product].id,
            qty_per_serving=i.qty,
            unit=i.unit,
            optional=i.optional,
        )
        for i in data.ingredients
    )
    await session.flush()
    return recipe, created


async def save_festival(
    session: AsyncSession,
    *,
    day: date,
    code: str,
    region: str,
    names: dict[str, str],
    rules: dict[str, bool],
    verified: bool,
) -> None:
    festival = await session.get(Festival, (day, code, region))
    if festival is None:
        festival = Festival(day=day, code=code, region=region)
        session.add(festival)
    festival.names = names
    festival.rules = rules
    festival.verified = verified
    await session.flush()


# --- reading ----------------------------------------------------------------------------------


async def list_recipes(
    session: AsyncSession,
    *,
    cuisine: str | None,
    slot: str | None,
    diet: str | None,
    jain_ok: bool | None,
    fasting_ok: bool | None,
    cursor: str | None,
    limit: int,
) -> tuple[list[Recipe], str | None]:
    english = Recipe.names["en"].astext
    stmt = select(Recipe).where(Recipe.active)
    if cuisine:
        stmt = stmt.where(Recipe.cuisine == cuisine)
    if slot:
        stmt = stmt.where(Recipe.slots.contains([slot]))
    if diet:
        stmt = stmt.where(Recipe.diet == diet)
    for rule, wanted in (("jain_ok", jain_ok), ("fasting_ok", fasting_ok)):
        if wanted is not None:
            stmt = stmt.where(Recipe.rules[rule].as_boolean() == wanted)
    if cursor is not None:
        after = decode_cursor(cursor).get("after")
        if not (isinstance(after, list) and len(after) == 2 and isinstance(after[0], str)):  # pyright: ignore[reportUnknownArgumentType]
            raise ProblemError(422, "request.invalid_cursor", "The cursor is not valid.")
        try:
            last_id = uuid.UUID(str(after[1]))  # pyright: ignore[reportUnknownArgumentType]
        except ValueError as exc:
            raise ProblemError(422, "request.invalid_cursor", "The cursor is not valid.") from exc
        stmt = stmt.where(tuple_(english, Recipe.id) > tuple_(after[0], last_id))
    rows = list(
        (await session.execute(stmt.order_by(english, Recipe.id).limit(limit + 1))).scalars()
    )
    found = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        next_cursor = encode_cursor({"after": [found[-1].names["en"], str(found[-1].id)]})
    return found, next_cursor


@dataclass(frozen=True)
class Ingredient:
    product: Product
    qty: Decimal
    unit: str
    optional: bool


async def get_recipe(
    session: AsyncSession, recipe_id: uuid.UUID
) -> tuple[Recipe, list[Ingredient]]:
    recipe = await session.get(Recipe, recipe_id)
    if recipe is None or not recipe.active:
        raise recipe_not_found()
    rows = await session.execute(
        select(RecipeIngredient, Product)
        .join(Product, Product.id == RecipeIngredient.product_id)
        .where(RecipeIngredient.recipe_id == recipe_id)
        .order_by(RecipeIngredient.optional, Product.names["en"].astext)
    )
    return recipe, [
        Ingredient(product, link.qty_per_serving, link.unit, link.optional)
        for link, product in rows.all()
    ]


async def cost_per_serving(
    session: AsyncSession, ingredients: list[Ingredient], hub_id: uuid.UUID | None
) -> int | None:
    """Sum of each ingredient's share of its product's step price; None if any price is missing."""
    prices = await prices_for(session, hub_id, [i.product.id for i in ingredients])
    total = Decimal(0)
    for ingredient in ingredients:
        price = prices.get(ingredient.product.id)
        if price is None:
            return None
        step = ingredient.product.step_qty * BASE_UNIT[ingredient.product.step_unit][1]
        total += ingredient.qty / step * price.price_paise
    return round(total)


async def festivals(
    session: AsyncSession, start: date, end: date, region: str | None
) -> list[Festival]:
    stmt = select(Festival).where(Festival.day >= start, Festival.day <= end)
    if region and region != "all":
        stmt = stmt.where(Festival.region.in_(["all", region]))
    return list((await session.execute(stmt.order_by(Festival.day, Festival.code))).scalars())
