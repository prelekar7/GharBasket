"""Facade of M2 Catalog & Pricing: the only module other modules may import."""

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog.models import Product, Recipe, RecipeIngredient
from gharbasket.modules.catalog.pricing import prices_for
from gharbasket.modules.catalog.recipes import BASE_UNIT, festivals
from gharbasket.modules.catalog.service import step_label

__all__ = [
    "LineProduct",
    "PlanCatalog",
    "PlanIngredient",
    "PlanProduct",
    "PlanRecipe",
    "festivals_between",
    "line_products",
    "plan_catalog",
    "product_ids_by_slug",
    "step_label",
]


async def product_ids_by_slug(session: AsyncSession, slugs: list[str]) -> dict[str, uuid.UUID]:
    """Active products' ids by slug; unknown slugs are left out."""
    if not slugs:
        return {}
    rows = await session.execute(
        select(Product.slug, Product.id).where(Product.slug.in_(slugs), Product.active)
    )
    return {slug: product_id for slug, product_id in rows.all()}


@dataclass(frozen=True)
class LineProduct:
    """What an order line needs from a product: its step, names and (optionally) hub price."""

    id: uuid.UUID
    slug: str
    names: dict[str, str]
    step_qty: int
    step_unit: str
    price_paise: int | None


async def line_products(
    session: AsyncSession, product_ids: list[uuid.UUID], hub_id: uuid.UUID | None = None
) -> dict[uuid.UUID, LineProduct]:
    """Active products by id, with today's price at `hub_id` when given; unknown ids left out."""
    if not product_ids:
        return {}
    rows = await session.execute(select(Product).where(Product.id.in_(product_ids), Product.active))
    products = list(rows.scalars())
    prices = await prices_for(session, hub_id, [p.id for p in products]) if hub_id else {}
    return {
        p.id: LineProduct(
            p.id,
            p.slug,
            dict(p.names),
            p.step_qty,
            p.step_unit,
            prices[p.id].price_paise if p.id in prices else None,
        )
        for p in products
    }


# --- what the weekly planner needs (M3 §3.1, M10 §3.1) ----------------------------------------


@dataclass(frozen=True)
class PlanProduct:
    id: uuid.UUID
    slug: str
    kind: str
    names: dict[str, str]
    step_qty: int
    step_unit: str
    tags: frozenset[str]
    shelf_life_h: int | None
    price_paise: int | None  # at the hub, today


@dataclass(frozen=True)
class PlanIngredient:
    product_id: uuid.UUID
    product_slug: str
    qty_per_serving: float  # base units
    optional: bool


@dataclass(frozen=True)
class PlanRecipe:
    id: uuid.UUID
    slug: str
    names: dict[str, str]
    cuisine: str
    slots: tuple[str, ...]
    cook_minutes: int
    servings: int
    diet: str
    rules: dict[str, bool]
    tags: tuple[str, ...]
    is_side: bool
    sides: tuple[str, ...]
    ingredients: tuple[PlanIngredient, ...]
    cost_per_serving_paise: int | None
    perishables: tuple[str, ...]  # product slugs with <= 96 h shelf life

    def as_plan_recipe(self) -> dict[str, Any]:
        """The ML service's `recipes[]` item (M10 §3.1)."""
        return {
            "slug": self.slug,
            "cuisine": self.cuisine,
            "slots": list(self.slots),
            "cook_minutes": self.cook_minutes,
            "diet": self.diet,
            "jain_ok": self.rules["jain_ok"],
            "no_onion_garlic": self.rules["no_onion_garlic"],
            "fasting_ok": self.rules["fasting_ok"],
            "diabetic_ok": self.rules["diabetic_ok"],
            "no_cook": "no_cook" in self.tags,
            "cost_per_serving_paise": self.cost_per_serving_paise or 0,
            "perishables": list(self.perishables),
        }


@dataclass(frozen=True)
class PlanCatalog:
    products: dict[uuid.UUID, PlanProduct]
    recipes: tuple[PlanRecipe, ...]  # mains only (sides are listed on each main)

    def product_by_slug(self, slug: str) -> PlanProduct | None:
        return next((p for p in self.products.values() if p.slug == slug), None)


PERISHABLE_H = 96


async def plan_catalog(session: AsyncSession, hub_id: uuid.UUID | None) -> PlanCatalog:
    """Every active product with today's hub price, and every active recipe with its
    ingredients and cost per serving; the planner's whole menu, loaded once per job run."""
    rows = await session.execute(select(Product).where(Product.active))
    products_rows = list(rows.scalars())
    prices = await prices_for(session, hub_id, [p.id for p in products_rows]) if hub_id else {}
    products = {
        p.id: PlanProduct(
            p.id,
            p.slug,
            p.kind,
            dict(p.names),
            p.step_qty,
            p.step_unit,
            frozenset(p.tags),
            p.shelf_life_h,
            prices[p.id].price_paise if p.id in prices else None,
        )
        for p in products_rows
    }
    recipes_rows = list(
        (await session.execute(select(Recipe).where(Recipe.active).order_by(Recipe.slug))).scalars()
    )
    links = await session.execute(
        select(RecipeIngredient).where(RecipeIngredient.recipe_id.in_([r.id for r in recipes_rows]))
    )
    by_recipe: dict[uuid.UUID, list[RecipeIngredient]] = defaultdict(list)
    for link in links.scalars():
        by_recipe[link.recipe_id].append(link)
    recipes = []
    for r in recipes_rows:
        ingredients = []
        cost = Decimal(0)
        priced = True
        perishables = []
        for link in sorted(by_recipe[r.id], key=lambda link: str(link.product_id)):
            product = products.get(link.product_id)
            if product is None:
                continue
            ingredients.append(
                PlanIngredient(product.id, product.slug, float(link.qty_per_serving), link.optional)
            )
            if product.price_paise is None:
                priced = False
            else:
                step = product.step_qty * BASE_UNIT[product.step_unit][1]
                cost += Decimal(link.qty_per_serving) / step * product.price_paise
            if product.shelf_life_h is not None and product.shelf_life_h <= PERISHABLE_H:
                perishables.append(product.slug)
        recipes.append(
            PlanRecipe(
                r.id,
                r.slug,
                dict(r.names),
                r.cuisine,
                tuple(r.slots),
                r.cook_minutes,
                r.servings,
                r.diet,
                dict(r.rules),
                tuple(r.tags),
                r.is_side,
                tuple(r.sides),
                tuple(ingredients),
                round(cost) if priced else None,
                tuple(sorted(perishables)),
            )
        )
    return PlanCatalog(products, tuple(recipes))


async def festivals_between(
    session: AsyncSession, start: date, end: date
) -> list[tuple[date, str, dict[str, bool]]]:
    """(day, code, rules) of every festival day in the range, any region."""
    return [(f.day, f.code, dict(f.rules)) for f in await festivals(session, start, end, None)]
