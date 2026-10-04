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

from gharbasket.modules.catalog.api import PlanCatalog, PlanIngredient
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
) -> dict[str, Any]:
    budget = profile.budget_paise
    return {
        "household": {
            "size": profile.size,
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
        "recipes": [r.as_plan_recipe() for r in catalog.recipes],
        "time_limit_s": 2.0,
    }


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


def lines_from_plan(
    profile: HouseholdPlanProfile,
    catalog: PlanCatalog,
    plan: list[dict[str, Any]] | None,
    *,
    fallback: bool,
) -> list[DraftLine]:
    """Ingredients of the planned recipes (and their sides) x size ÷ servings, in steps, plus the
    rule table for daily use and two fruits. `fallback`: no plan, so only the rule table."""
    by_slug = {r.slug: r for r in catalog.recipes}
    needed: dict[uuid.UUID, float] = defaultdict(float)  # base units
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

    equivalents = rules.adult_equivalents([Member(m) for m in profile.member_age_bands])
    daily = rules.daily_use_plan(equivalents)
    for slug, base_units in daily.items():
        product = catalog.product_by_slug(slug)
        if product is not None:
            needed[product.id] += base_units
    fruits = [p for p in catalog.products.values() if p.kind == "fruit" and p.price_paise]
    fruits.sort(key=lambda p: (p.price_paise or 0, p.slug))
    per_fruit = (
        rules.FRUIT_PER_ADULT_WEEK_G * equivalents / max(1, min(rules.FRUIT_KINDS, len(fruits)))
    )
    for product in fruits[: rules.FRUIT_KINDS]:
        needed[product.id] += per_fruit

    lines: list[DraftLine] = []
    for product_id, base_units in sorted(needed.items(), key=lambda kv: str(kv[0])):
        product = catalog.products.get(product_id)
        if product is None or product.price_paise is None:
            continue  # no price at this hub: the hub manager's problem, not the customer's
        qty = rules.steps_needed(base_units, product.step_qty, BASE_FACTOR[product.step_unit])
        if product.slug in daily or product.kind == "fruit":
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
    "plan_request",
]
