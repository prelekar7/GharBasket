"""Facade of M2 Catalog & Pricing: the only module other modules may import."""

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog.models import (
    Category,
    Product,
    Recipe,
    RecipeIngredient,
    TiffinMenu,
)
from gharbasket.modules.catalog.pricing import latest_prices_on, prices_for
from gharbasket.modules.catalog.recipes import BASE_UNIT, festivals
from gharbasket.modules.catalog.service import search, step_label
from gharbasket.modules.catalog.substitutes import substitutes
from gharbasket.platform.errors import ProblemError

__all__ = [
    "LineProduct",
    "PlanCatalog",
    "PlanIngredient",
    "PlanProduct",
    "PlanRecipe",
    "ProductMatch",
    "StockProduct",
    "TiffinMenuRef",
    "festivals_between",
    "find_products",
    "latest_prices_on",
    "line_grams",
    "line_products",
    "plan_catalog",
    "product_ids_by_slug",
    "step_label",
    "stock_products",
    "substitute_ids",
    "tiffin_menu",
    "tiffin_menus",
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
    # (category sort order, category slug): how packers walk the shelves (M6 §3.2)
    shelf: tuple[int, str] = (0, "")


async def line_products(
    session: AsyncSession, product_ids: list[uuid.UUID], hub_id: uuid.UUID | None = None
) -> dict[uuid.UUID, LineProduct]:
    """Active products by id, with today's price at `hub_id` when given; unknown ids left out."""
    if not product_ids:
        return {}
    rows = await session.execute(
        select(Product, Category.sort_order, Category.slug)
        .join(Category, Category.id == Product.category_id)
        .where(Product.id.in_(product_ids), Product.active)
    )
    found = rows.all()
    prices = await prices_for(session, hub_id, [p.id for p, _, _ in found]) if hub_id else {}
    return {
        p.id: LineProduct(
            p.id,
            p.slug,
            dict(p.names),
            p.step_qty,
            p.step_unit,
            prices[p.id].price_paise if p.id in prices else None,
            (order, category),
        )
        for p, order, category in found
    }


@dataclass(frozen=True)
class StockProduct:
    """What M6 needs to buy, receive and count a product (M6 §3.3, §3.4)."""

    id: uuid.UUID
    slug: str
    kind: str
    names: dict[str, str]
    step_qty: int
    step_unit: str
    shelf_life_h: int | None
    unit_g: float | None  # grams per piece or bunch, from the nutrition data
    shelf: tuple[int, str]  # (category sort order, category slug): how the hub's shelves run
    active: bool


async def stock_products(
    session: AsyncSession, product_ids: list[uuid.UUID]
) -> dict[uuid.UUID, StockProduct]:
    """Products by id, active or not (stock bought before a product was retired still counts);
    unknown ids are left out."""
    if not product_ids:
        return {}
    rows = await session.execute(
        select(Product, Category.sort_order, Category.slug)
        .join(Category, Category.id == Product.category_id)
        .where(Product.id.in_(product_ids))
    )
    return {
        p.id: StockProduct(
            p.id,
            p.slug,
            p.kind,
            dict(p.names),
            p.step_qty,
            p.step_unit,
            p.shelf_life_h,
            (p.nutrition or {}).get("unit_g"),
            (order, category),
            p.active,
        )
        for p, order, category in rows.all()
    }


@dataclass(frozen=True)
class ProductMatch:
    """A product a person may mean, with today's price at their hub."""

    id: uuid.UUID
    slug: str
    names: dict[str, str]
    step_qty: int
    step_unit: str
    price_paise: int
    exact: bool  # the words are its slug or one of its spellings exactly
    score: float  # how well the words match (1.0 for an exact spelling)


async def find_products(
    session: AsyncSession, words: str, hub_id: uuid.UUID | None, *, limit: int = 4
) -> list[ProductMatch]:
    """Products the hub sells today that `words` may name (a slug, or any spelling in English,
    Hindi, Marathi or Hinglish), best first; products without a price at the hub are left out."""
    term = " ".join(words.split())
    if not term or hub_id is None:
        return []
    slug = term.lower().replace(" ", "-")
    exact_ids = await product_ids_by_slug(session, [slug])
    if exact_ids:
        ranked = [(exact_ids[slug], True, 1.0)]
    else:
        hits = await search(session, term, limit=limit)
        ranked = [(h.product.id, h.matched.lower() == term.lower(), h.score) for h in hits]
    products = await line_products(session, [pid for pid, _, _ in ranked], hub_id=hub_id)
    found = []
    for pid, exact, score in ranked:
        product = products.get(pid)
        if product is not None and product.price_paise is not None:
            found.append(
                ProductMatch(
                    product.id,
                    product.slug,
                    product.names,
                    product.step_qty,
                    product.step_unit,
                    product.price_paise,
                    exact,
                    score,
                )
            )
    return found


async def substitute_ids(
    session: AsyncSession, product_id: uuid.UUID, hub_id: uuid.UUID | None, limit: int = 10
) -> list[uuid.UUID]:
    """The ranked substitutes of a product (what `GET /v1/products/{id}/substitutes` shows);
    empty for an unknown product. M3 accepts a swap only from this list."""
    try:
        ranked = await substitutes(session, product_id, hub_id, limit)
    except ProblemError:
        return []
    return [c.product.id for c in ranked]


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
    nutrition: dict[str, Any] | None = None  # per 100 g (IFCT 2017), unit_g for pieces


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
    # kcal, protein_g, fibre_g from the ingredients' IFCT rows; None when any required
    # ingredient has no nutrition data
    nutrition_per_serving: dict[str, float] | None = None

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
            "nutrition_per_serving": self.nutrition_per_serving,
            "sides": list(self.sides),
            "is_side": self.is_side,
        }


@dataclass(frozen=True)
class PlanCatalog:
    products: dict[uuid.UUID, PlanProduct]
    recipes: tuple[PlanRecipe, ...]  # mains only (sides are listed on each main)

    def product_by_slug(self, slug: str) -> PlanProduct | None:
        return next((p for p in self.products.values() if p.slug == slug), None)


PERISHABLE_H = 96


def grams_of(product: PlanProduct, qty_base_units: float) -> float | None:
    """A recipe quantity (in the product's base unit: g, ml, piece or bunch) in grams; ml count
    as grams, a piece or bunch needs the product's `unit_g`."""
    base = BASE_UNIT[product.step_unit][0]
    if base in ("g", "ml"):
        return qty_base_units
    unit_g = (product.nutrition or {}).get("unit_g")
    return None if unit_g is None else qty_base_units * unit_g


async def line_grams(
    session: AsyncSession, lines: list[tuple[uuid.UUID, int, int, str]]
) -> list[float | None]:
    """The weight in grams of order lines given as (product id, steps, step_qty, step_unit), by
    `grams_of`'s rule (M7's van loads): g and ml by the unit, a piece or bunch through the
    product's `unit_g`; None where that is unknown (a pack, or a piece without `unit_g`)."""
    ids = sorted({product_id for product_id, *_ in lines})
    rows = await session.execute(select(Product.id, Product.nutrition).where(Product.id.in_(ids)))
    unit_g = {pid: (nutrition or {}).get("unit_g") for pid, nutrition in rows.all()}
    weights: list[float | None] = []
    for product_id, steps, step_qty, step_unit in lines:
        if step_unit not in BASE_UNIT:  # a pack has no weight of its own
            weights.append(None)
            continue
        base, factor = BASE_UNIT[step_unit]
        per_unit = 1 if base in ("g", "ml") else unit_g.get(product_id)
        weight = None if per_unit is None else steps * step_qty * factor * float(per_unit)
        weights.append(weight)
    return weights


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
            p.nutrition,
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
        nutrition = {"kcal": 0.0, "protein_g": 0.0, "fibre_g": 0.0}
        nourished = True
        for link in sorted(by_recipe[r.id], key=lambda link: str(link.product_id)):
            product = products.get(link.product_id)
            if product is None:
                continue
            ingredients.append(
                PlanIngredient(product.id, product.slug, float(link.qty_per_serving), link.optional)
            )
            grams = grams_of(product, float(link.qty_per_serving))
            if product.nutrition is None or grams is None:
                nourished = nourished and link.optional
            else:
                for key in nutrition:
                    nutrition[key] += product.nutrition[key] * grams / 100
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
                {k: round(v, 1) for k, v in nutrition.items()} if nourished else None,
            )
        )
    return PlanCatalog(products, tuple(recipes))


async def festivals_between(
    session: AsyncSession, start: date, end: date
) -> list[tuple[date, str, dict[str, bool]]]:
    """(day, code, rules) of every festival day in the range, any region."""
    return [(f.day, f.code, dict(f.rules)) for f in await festivals(session, start, end, None)]


# --- tiffin menus (§4; booked through M4 §3.5) ---------------------------------------------------


@dataclass(frozen=True)
class TiffinMenuRef:
    """A kitchen's tiffin offer for a date and meal, with its dishes' names {en, hi, mr}."""

    id: uuid.UUID
    hub_id: uuid.UUID
    partner_id: uuid.UUID
    on_date: date
    meal: str
    product_id: uuid.UUID
    price_paise: int  # per portion
    status: str
    dishes: tuple[tuple[uuid.UUID, dict[str, str]], ...]  # (recipe_id, names), menu order


async def _refs(session: AsyncSession, menus: list[TiffinMenu]) -> list[TiffinMenuRef]:
    ids = {rid for menu in menus for rid in menu.recipe_ids}
    names: dict[uuid.UUID, dict[str, str]] = {}
    if ids:
        rows = await session.execute(select(Recipe.id, Recipe.names).where(Recipe.id.in_(ids)))
        names = {rid: dict(n) for rid, n in rows.all()}
    return [
        TiffinMenuRef(
            menu.id,
            menu.hub_id,
            menu.partner_id,
            menu.on_date,
            menu.meal,
            menu.product_id,
            menu.price_paise,
            menu.status,
            tuple((rid, names[rid]) for rid in menu.recipe_ids if rid in names),
        )
        for menu in menus
    ]


async def tiffin_menus(
    session: AsyncSession, hub_id: uuid.UUID, first: date, last: date, meal: str | None = None
) -> list[TiffinMenuRef]:
    """A hub's approved menus from `first` to `last`, by date, meal and id."""
    stmt = select(TiffinMenu).where(
        TiffinMenu.hub_id == hub_id,
        TiffinMenu.on_date.between(first, last),
        TiffinMenu.status == "approved",
    )
    if meal is not None:
        stmt = stmt.where(TiffinMenu.meal == meal)
    rows = await session.execute(stmt.order_by(TiffinMenu.on_date, TiffinMenu.meal, TiffinMenu.id))
    return await _refs(session, list(rows.scalars()))


async def tiffin_menu(session: AsyncSession, menu_id: uuid.UUID) -> TiffinMenuRef | None:
    """Any menu by id, whatever its status (the caller checks it is approved)."""
    menu = await session.get(TiffinMenu, menu_id)
    return None if menu is None else (await _refs(session, [menu]))[0]
