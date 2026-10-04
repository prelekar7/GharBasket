"""What the simulator needs from M2 Catalog, read through the catalog services.

The recipes come out in the shape of the planner's request (M10 §3.1), sorted by slug so the
same database always gives the same world.
"""

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog import recipes as catalog_recipes
from gharbasket.modules.catalog import service as catalog_service
from gharbasket.modules.catalog.pricing import prices_for
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
    step: float = 1.0  # base units in one step (what a box line counts)
    price_paise: int | None = None  # per step at the hub, today
    shelf_life_h: int | None = None
    category: str = ""  # category slug (a substitute comes from the same category)
    unit_g: int | None = None  # grams of one piece or bunch (IFCT mapping)

    def base_units(self, grams: float) -> float:
        """Grams in this product's base unit (pieces and bunches by their weight)."""
        if self.unit in ("g", "ml"):
            return grams
        return grams / (self.unit_g or 100)

    @property
    def perishable(self) -> bool:
        return self.shelf_life_h is not None and self.shelf_life_h <= PERISHABLE_H


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
    found_products = []
    cursor = None
    while True:
        rows, cursor = await catalog_service.list_products(
            session, kind=None, category=None, tag=None, cursor=cursor, limit=PAGE
        )
        found_products += [(row.product, row.category.slug) for row in rows]
        if cursor is None:
            break
    prices = await prices_for(session, hub_id, [p.id for p, _ in found_products])
    products: dict[str, Product] = {
        p.slug: Product(
            p.slug,
            BASE_UNIT[p.step_unit][0],
            p.kind,
            frozenset(p.tags),
            p.step_qty * BASE_UNIT[p.step_unit][1],
            prices[p.id].price_paise if p.id in prices else None,
            p.shelf_life_h,
            category,
            (p.nutrition or {}).get("unit_g"),
        )
        for p, category in found_products
    }

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


SEED_FILES = Path(__file__).resolve().parents[4] / "data" / "seeds" / "catalog"


def from_seed_files(seed: Path = SEED_FILES) -> Catalog:
    """The seed catalog read straight from data/seeds/catalog (no database): what the evaluation
    harness and the tests use; `load_catalog` gives the same after `just seed`."""
    raw = json.loads((seed / "products.json").read_text())["products"]
    steps = {p["slug"]: p["step"]["qty"] * BASE_UNIT[p["step"]["unit"]][1] for p in raw}
    shelf = {p["slug"]: p["shelf_life_h"] for p in raw}
    prices = {
        i["slug"]: i["price_paise"] for i in json.loads((seed / "prices.json").read_text())["items"]
    }
    unit_g = {
        n["slug"]: n.get("unit_g")
        for n in json.loads((seed / "nutrition.json").read_text())["items"]
    }
    products = {
        p["slug"]: Product(
            p["slug"],
            BASE_UNIT[p["step"]["unit"]][0],
            p["kind"],
            frozenset(p["tags"]),
            steps[p["slug"]],
            prices.get(p["slug"]),
            shelf[p["slug"]],
            p["category"],
            unit_g.get(p["slug"]),
        )
        for p in raw
    }
    recipes: list[dict[str, Any]] = []
    ingredients: dict[str, tuple[Ingredient, ...]] = {}
    for r in sorted(
        json.loads((seed / "recipes.json").read_text())["recipes"], key=lambda r: r["slug"]
    ):
        if r.get("is_side"):
            continue
        tags = set().union(*(products[i["product"]].tags for i in r["ingredients"]))
        no_onion_garlic = "onion_garlic" not in tags
        diet = "egg" if "egg" in tags else "veg"
        recipes.append(
            {
                "slug": r["slug"],
                "cuisine": r["cuisine"],
                "slots": r["slots"],
                "cook_minutes": r["cook_minutes"],
                "diet": diet,
                "jain_ok": no_onion_garlic and "root" not in tags and diet == "veg",
                "no_onion_garlic": no_onion_garlic,
                "fasting_ok": r["fasting_ok"],
                "diabetic_ok": r["diabetic_ok"],
                "no_cook": "no_cook" in r["tags"],
                "cost_per_serving_paise": round(
                    sum(
                        i["qty"] / steps[i["product"]] * prices[i["product"]]
                        for i in r["ingredients"]
                    )
                ),
                "perishables": sorted(
                    i["product"] for i in r["ingredients"] if shelf[i["product"]] <= PERISHABLE_H
                ),
            }
        )
        ingredients[r["slug"]] = tuple(
            sorted(
                Ingredient(i["product"], float(i["qty"]), i["optional"]) for i in r["ingredients"]
            )
        )
    festivals = [
        Festival(date.fromisoformat(f["date"]), f["code"], f["rules"])
        for f in json.loads((seed / "festivals.json").read_text())["festivals"]
    ]
    return Catalog(products, recipes, ingredients, festivals)
