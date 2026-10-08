"""From a household and the catalog to a week's box (M3 §3.1 "How a cycle is generated").

1. Build the planner's request from the household's rules (M1), the fasting calendar (M2)
   and the hub's recipes with today's prices (M2).
2. Ask the planner through the gateway (CP-SAT, or the rules fallback), recording the answer.
3. Turn the plan into lines: ingredients x size ÷ servings, summed over the week, in steps;
   plus the family-size rule table for daily use; plus two fruits in season.

Pure where it can be: `lines_from_plan` and `fasting_days` take data and return data.
"""

import math
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from gharbasket.modules.catalog.api import (
    PERISHABLE_H,
    PlanCatalog,
    PlanIngredient,
    PlanProduct,
    PlanRecipe,
)
from gharbasket.modules.identity.api import HouseholdPlanProfile
from gharbasket.modules.subscriptions import rules
from gharbasket.modules.subscriptions.rules import Member

SLOTS = ("breakfast", "lunch", "dinner")
BASE_FACTOR = {"g": 1, "kg": 1000, "ml": 1, "l": 1000, "piece": 1, "bunch": 1, "pack": 1}


def fasting_days(
    profile: HouseholdPlanProfile, monday: date, festivals: list[tuple[date, str, dict[str, bool]]]
) -> list[dict[str, Any]]:
    """The planner's fasting days for this week, from the household's fasts and the calendar."""
    days: dict[int, bool] = {}
    for d in range(7):
        day = monday + timedelta(days=d)
        if "tuesdays" in profile.fasts and d == 1:
            days[d] = profile.no_onion_garlic_on_fasts
        if "shravan_mondays" in profile.fasts and d == 0 and day.month in (7, 8):
            days[d] = days.get(d, False) or profile.no_onion_garlic_on_fasts
        for festival_day, code, festival_rules in festivals:
            if festival_day == day and code in profile.fasts and festival_rules.get("fasting"):
                strict = profile.no_onion_garlic_on_fasts or bool(
                    festival_rules.get("no_onion_garlic")
                )
                days[d] = days.get(d, False) or strict
    return [{"day": d, "no_onion_garlic": strict} for d, strict in sorted(days.items())]


def plan_request(
    profile: HouseholdPlanProfile,
    catalog: PlanCatalog,
    monday: date,
    festivals: list[tuple[date, str, dict[str, bool]]],
    price_index: dict[uuid.UUID, float] | None = None,
    preferences: dict[str, float] | None = None,
) -> dict[str, Any]:
    """The planner's request (M10 §3.1 + v1 §3.2 + v2): the household's rules and members, the
    week's fasting days, every active recipe (sides flagged, never planned alone) with its
    nutrition, the price spike of its costliest ingredient from `intel.price_index` and, v2,
    the household's learned liking of the dish (`preferences` by recipe slug, from the ranker)."""
    budget = profile.budget_paise
    return {
        "household": {
            "size": profile.size,
            "members": [{"age_band": band, "sex": "other"} for band in profile.member_age_bands],
            "diet": profile.diet,
            "jain": profile.jain,
            "diabetic": profile.diabetic,
            "cuisines": list(profile.cuisines),
            "weekday_cook_minutes": profile.weekday_cook_minutes,
            "weekend_cook_minutes": profile.weekend_cook_minutes,
            "no_cook_days": list(profile.no_cook_days),
            # The Budget Lock is monthly; the planner works a week at a time.
            "weekly_budget_paise": None if budget is None else budget * 7 // 30,
        },
        "fasting_days": fasting_days(profile, monday, festivals),
        "recipes": [
            {
                **r.as_plan_recipe(),
                "price_spike": price_spike(r, catalog.products, price_index or {}),
                "preference": (preferences or {}).get(r.slug),
            }
            for r in catalog.recipes
        ],
        "time_limit_s": 1.0,
    }


def recipe_preferences(scores: dict[uuid.UUID, float], catalog: PlanCatalog) -> dict[str, float]:
    """v2: a dish's preference is the mean ranker score of its scored ingredients, then put on
    0..1 by rank across the mains (the planner's liking term wants a comparable scale). Dishes
    with no scored ingredient are left out (unknown)."""
    raw: dict[str, float] = {}
    for recipe in catalog.recipes:
        if recipe.is_side:
            continue
        found = [scores[i.product_id] for i in recipe.ingredients if i.product_id in scores]
        if found:
            raw[recipe.slug] = sum(found) / len(found)
    distinct = sorted(set(raw.values()))
    if len(distinct) < 2:
        return dict.fromkeys(raw, 1.0)
    scale = {value: round(i / (len(distinct) - 1), 3) for i, value in enumerate(distinct)}
    return {slug: scale[value] for slug, value in raw.items()}  # ties share a value


def price_spike(
    recipe: PlanRecipe,
    products: dict[uuid.UUID, PlanProduct],
    price_index: dict[uuid.UUID, float],
) -> float | None:
    """(price - 4-week index) / index of the recipe's costliest ingredient, the one with the
    largest qty x today's price per serving (M10 §3.2); None when that ingredient is not above
    its usual price. A spiked garnish (2 g of coriander) never marks the dish."""
    costs = [
        (
            i.qty_per_serving / (p.step_qty * BASE_FACTOR[p.step_unit]) * p.price_paise,
            i.product_slug,
            i,
        )
        for i in recipe.ingredients
        if (p := products.get(i.product_id)) is not None and p.price_paise is not None
    ]
    if not costs:
        return None
    costliest = max(costs, key=lambda c: (c[0], c[1]))[2]
    index = price_index.get(costliest.product_id)
    return round(index - 1.0, 3) if index is not None and index > 1.0 else None


@dataclass
class DraftLine:
    product_id: uuid.UUID
    qty: int
    step_qty: int
    step_unit: str
    unit_price_paise: int
    reason_code: str
    reason_params: dict[str, Any] | None
    source: str

    @property
    def total_paise(self) -> int:
        return self.qty * self.unit_price_paise


@dataclass
class Draft:
    lines: list[DraftLine]
    meal_plan: list[dict[str, Any]]
    highlights: list[dict[str, Any]] = field(default_factory=list)

    @property
    def total_paise(self) -> int:
        return sum(line.total_paise for line in self.lines)


def meal_plan_days(
    plan: list[dict[str, Any]] | None, catalog: PlanCatalog, monday: date
) -> list[dict[str, Any]]:
    """The week as the app shows it: per day, per slot, the recipe (None = tiffin or no plan)."""
    by_slug = {r.slug: r for r in catalog.recipes}
    chosen: dict[tuple[int, str], str | None] = {}
    for meal in plan or []:
        chosen[(int(meal["day"]), str(meal["slot"]))] = meal.get("recipe")
    days = []
    for d in range(7):
        meals = []
        for slot in SLOTS:
            slug = chosen.get((d, slot))
            recipe = by_slug.get(slug) if slug else None
            meals.append(
                {
                    "slot": slot,
                    "recipe_id": None if recipe is None else str(recipe.id),
                    "recipe_slug": slug,
                    "tiffin": plan is not None and slug is None and slot != "breakfast",
                    "sides": list(recipe.sides) if recipe else [],
                }
            )
        days.append({"date": (monday + timedelta(days=d)).isoformat(), "meals": meals})
    return days


# Carry-over (M3 §3.1 step 2). Only a dry staple is counted as still in the cupboard: one that
# lasts well past the CARRY_WEEKS window. The seed catalog has a clear gap here: fresh produce
# and dairy go up to 720 h (tomato, green chilli, bottle gourd 120 h, lemon 168 h, ginger and
# eggs 336 h, onion, potato and garlic 720 h) and the dry pantry starts at 2160 h (atta; then
# besan, dals, rice, oil, spices, salt). Produce is bought for the week, so it never carries.
# (A different line from catalog.api.PERISHABLE_H, 96 h: what spoils within the week.)
CARRY_MIN_SHELF_H = 720
# How far back the cupboard is counted (v0): a purchase older than this is assumed used up, so
# a slow staple (salt, spices) is bought again once its last purchase leaves the window.
CARRY_WEEKS = 3


DEFAULT_UNIT_G = 100  # a piece or bunch without a known weight


def in_base_units(product: PlanProduct, grams: float) -> float:
    """Grams in the product's base unit: as they are for g and ml, divided by the weight of one
    piece or bunch (`unit_g` from the IFCT mapping) otherwise."""
    if product.step_unit in ("g", "kg", "ml", "l"):
        return grams
    unit_g = (product.nutrition or {}).get("unit_g") or DEFAULT_UNIT_G
    return grams / unit_g


def carries_over(product: PlanProduct) -> bool:
    """A dry staple, whose leftovers count against the next weeks' lines."""
    return product.shelf_life_h is not None and product.shelf_life_h > CARRY_MIN_SHELF_H


def left_over(bought: list[tuple[float, float]], weekly_need: float) -> float:
    """Base units still in the cupboard now: `bought` holds (weeks ago, base units) per delivery;
    each delivery adds to the cupboard and every week in between uses `weekly_need` of it,
    never below zero. Nothing is assumed from before the oldest delivery.

    v0 approximation: the caller passes this week's need as the rate for every past week. Exact
    for the rule table (the same every week); for a staple the recipes also use (atta for
    chapati, a dal) last weeks' plans may have needed more or less, until the pantry twin
    (M10 F13) tracks the cupboard itself."""
    stock, last = 0.0, None
    for weeks_ago, qty in sorted(bought, reverse=True):  # oldest first
        if last is not None:
            stock = max(0.0, stock - weekly_need * (last - weeks_ago))
        stock, last = stock + qty, weeks_ago
    return 0.0 if last is None else max(0.0, stock - weekly_need * last)


def perishable(product: PlanProduct) -> bool:
    """Spoils within the week (catalog.api.PERISHABLE_H, 96 h): the pantry twin's scope."""
    return product.shelf_life_h is not None and product.shelf_life_h <= PERISHABLE_H


def plan_of(meal_plan: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """A stored week (`meal_plan_days`) back as the planner's meals, for `recipe_need`."""
    return [
        {"day": d, "slot": meal["slot"], "recipe": meal["recipe_slug"]}
        for d, day in enumerate(meal_plan or [])
        for meal in day.get("meals", [])
        if meal.get("recipe_slug")
    ]


def recipe_need(
    profile: HouseholdPlanProfile, catalog: PlanCatalog, plan: list[dict[str, Any]] | None
) -> tuple[dict[uuid.UUID, float], dict[uuid.UUID, set[str]]]:
    """Base units of each product the planned recipes (and their sides) need for the household:
    ingredients x size ÷ servings, optional ones left out; and which dishes need it."""
    by_slug = {r.slug: r for r in catalog.recipes}
    needed: dict[uuid.UUID, float] = defaultdict(float)
    recipes_for: dict[uuid.UUID, set[str]] = defaultdict(set)
    for meal in plan or []:
        slug = meal.get("recipe")
        if not slug or slug not in by_slug:
            continue
        for recipe_slug in (slug, *by_slug[slug].sides):
            recipe = by_slug.get(recipe_slug)
            if recipe is None:
                continue
            factor = profile.size / max(recipe.servings, 1)
            for ingredient in recipe.ingredients:
                if ingredient.optional:
                    continue
                needed[ingredient.product_id] += ingredient.qty_per_serving * factor
                recipes_for[ingredient.product_id].add(slug)
    return needed, recipes_for


def twin_product(product: PlanProduct) -> bool:
    """The pantry twin v0 drafts only perishable daily-use items (milk, curd): what a box
    delivers of them is used or wasted within the week, so the delivered daily-use share and
    the tap say how much the household goes through. Staples carry over in the cupboard and
    stay on the rule table until v1 models stock (M10 §3.3)."""
    return product.slug in rules.DAILY_USE_PER_ADULT_WEEK and perishable(product)


def lines_from_plan(
    profile: HouseholdPlanProfile,
    catalog: PlanCatalog,
    plan: list[dict[str, Any]] | None,
    *,
    fallback: bool,
    bought: dict[uuid.UUID, list[tuple[float, float]]] | None = None,
    quantities: dict[uuid.UUID, tuple[float, dict[str, Any]]] | None = None,
) -> list[DraftLine]:
    """Ingredients of the planned recipes (and their sides) x size ÷ servings, in steps, plus the
    rule table for daily use and two fruits. `fallback`: no plan, so only the rule table.
    `bought`: per dry staple, (weeks ago, base units) of the household's recent boxes (M3 §3.1
    step 2). This week's need stands for each week since: what is left is subtracted, so the
    line asks for the need minus the leftover, and none when enough is left.
    `quantities`: the pantry twin's weekly daily use per product (base units, with its params):
    it replaces the rule table's daily-use quantity, and the recipes' need still comes on top
    (`reason_code: pantry_low`)."""
    needed, recipes_for = recipe_need(profile, catalog, plan)

    equivalents = rules.adult_equivalents([Member(m) for m in profile.member_age_bands])
    daily = rules.daily_use_plan(equivalents)
    twin: dict[uuid.UUID, dict[str, Any]] = {}
    for slug, base_units in daily.items():
        product = catalog.product_by_slug(slug)
        if product is None:
            continue
        if quantities and product.id in quantities:
            base_units, params = quantities[product.id]
            twin[product.id] = params
        needed[product.id] += base_units
    fruits = [p for p in catalog.products.values() if p.kind == "fruit" and p.price_paise]
    fruits.sort(key=lambda p: (p.price_paise or 0, p.slug))
    per_fruit = (
        rules.FRUIT_PER_ADULT_WEEK_G * equivalents / max(1, min(rules.FRUIT_KINDS, len(fruits)))
    )
    for product in fruits[: rules.FRUIT_KINDS]:
        needed[product.id] += in_base_units(product, per_fruit)

    lines: list[DraftLine] = []
    for product_id, base_units in sorted(needed.items(), key=lambda kv: str(kv[0])):
        product = catalog.products.get(product_id)
        if product is None or product.price_paise is None:
            continue  # no price at this hub: the hub manager's problem, not the customer's
        if bought and product_id in bought and carries_over(product):
            base_units -= left_over(bought[product_id], base_units)
            if base_units <= 0:
                continue  # enough still in the cupboard
        qty = rules.steps_needed(base_units, product.step_qty, BASE_FACTOR[product.step_unit])
        if product_id in twin:
            reason, params, source = "pantry_low", twin[product_id], "ai"
        elif product.slug in daily or product.kind == "fruit":
            reason, params, source = "daily_use", None, "rule"
        else:
            reason = "fallback_rules" if fallback else "meal_plan"
            params = {"recipes": sorted(recipes_for[product_id])}
            source = "rule" if fallback else "ai"
        lines.append(
            DraftLine(
                product_id,
                qty,
                product.step_qty,
                product.step_unit,
                product.price_paise,
                reason,
                params,
                source,
            )
        )
    return lines


def highlights_for(
    plan_source: str, fasting: list[dict[str, Any]], budget_near: bool
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if fasting:
        out.append({"code": "festival", "params": {"days": [f["day"] for f in fasting]}})
    if budget_near:
        out.append({"code": "budget_near", "params": {}})
    if plan_source == "fallback":
        out.append({"code": "fallback_rules", "params": {}})
    return out


def ceil_div(a: float, b: float) -> int:
    return math.ceil(a / b) if b else 0


__all__ = [
    "Draft",
    "DraftLine",
    "PlanIngredient",
    "ceil_div",
    "fasting_days",
    "highlights_for",
    "lines_from_plan",
    "meal_plan_days",
    "plan_of",
    "plan_request",
    "recipe_need",
    "twin_product",
]
