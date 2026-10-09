"""The rule-based weekly planner: what customers get when the ML service is slow, down or unset.

Same hard rules as the CP-SAT planner (M10 §3.1) and the same comfort-rule relaxations, but
greedy: day by day, it takes the best allowed dish (household cuisine first, then cheapest) that
keeps the no-repeat windows, placing a fasting dish first on fasting days. Valid, not optimal.
"""

import math
from typing import Literal

from gharbasket.modules.intel_gateway.schemas import (
    PantryQuantities,
    PantryQuantity,
    PantryRequest,
    PlannedMeal,
    PlanRecipe,
    PlanRequest,
    Rule,
    Slot,
    WeekPlan,
)

SLOTS: tuple[Slot, ...] = ("breakfast", "lunch", "dinner")
WEEKEND = (5, 6)
WINDOWS = {"breakfast": 2, "main": 3}
FALLBACK: tuple[Rule, ...] = ("repeat", "cook_time", "budget")
MODEL = "rules-v0"

Plan = dict[tuple[int, Slot], str | None]


HardRule = Literal["diet", "jain", "fasting", "diabetic"]


def rule_breaks(
    req: PlanRequest, r: PlanRecipe, day: int, slot: str, *, every_meal_fasts: bool = False
) -> list[HardRule]:
    """The household's hard rules (M1) that `r` breaks at that meal. The planner needs one
    fasting dish per fasting day; `every_meal_fasts` holds every meal of it to `fasting_ok`
    (a meal the customer picks, M3 §3.2)."""
    h = req.household
    broken: list[HardRule] = []
    if (h.diet == "veg" and r.diet != "veg") or (h.diet == "egg" and r.diet == "nonveg"):
        broken.append("diet")
    if h.jain and not r.jain_ok:
        broken.append("jain")
    fast = next((f for f in req.fasting_days if f.day == day), None)
    if fast is not None and (
        (fast.no_onion_garlic and not r.no_onion_garlic) or (every_meal_fasts and not r.fasting_ok)
    ):
        broken.append("fasting")
    if h.diabetic and slot != "breakfast" and not r.diabetic_ok:
        broken.append("diabetic")
    return broken


def cook_limit(req: PlanRequest, day: int) -> int:
    h = req.household
    return h.weekend_cook_minutes if day in WEEKEND else h.weekday_cook_minutes


def _allowed(req: PlanRequest, r: PlanRecipe, day: int, slot: Slot, relax: frozenset[Rule]) -> bool:
    if slot not in r.slots or rule_breaks(req, r, day, slot):
        return False
    if "cook_time" not in relax and r.cook_minutes > cook_limit(req, day):
        return False
    if day in req.household.no_cook_days:
        return slot == "breakfast" and r.no_cook
    return True


def _rank(r: PlanRecipe, cuisines: set[str], by_cost: bool) -> tuple[bool, int, str]:
    """Household cuisine first (unless only cost counts), then cheapest, then by name."""
    return (not by_cost and r.cuisine not in cuisines, r.cost_per_serving_paise, r.slug)


def next_recipe(req: PlanRequest, day: int, slot: str, exclude: set[str]) -> PlanRecipe | None:
    """The one-tap "Swap meal" (M3 §3.2): the cheapest recipe of `req.recipes` for that meal
    that keeps every hard rule, fits the day's cook minutes and is not in `exclude`."""
    options = [
        r
        for r in req.recipes
        if r.slug not in exclude
        and slot in r.slots
        and not rule_breaks(req, r, day, slot, every_meal_fasts=True)
        and r.cook_minutes <= cook_limit(req, day)
    ]
    return min(options, key=lambda r: _rank(r, set(), by_cost=True), default=None)


def _repeats(plan: Plan, day: int, slot: Slot, slug: str, relax: frozenset[Rule]) -> bool:
    group: tuple[Slot, ...] = ("breakfast",) if slot == "breakfast" else ("lunch", "dinner")
    window = 1 if "repeat" in relax else WINDOWS["breakfast" if slot == "breakfast" else "main"]
    return any(
        plan.get((d, s)) == slug for d in range(max(0, day - window + 1), day + 1) for s in group
    )


def _greedy(req: PlanRequest, relax: frozenset[Rule], by_cost: bool) -> Plan | None:
    h = req.household
    cuisines = set(h.cuisines)
    fasting_days = {f.day for f in req.fasting_days}

    plan: Plan = {}
    for day in range(7):
        needs_fast = day in fasting_days
        for slot in SLOTS:
            if day in h.no_cook_days and slot != "breakfast":
                plan[day, slot] = None
                continue
            options = sorted(
                (r for r in req.recipes if _allowed(req, r, day, slot, relax)),
                key=lambda r: (needs_fast and not r.fasting_ok, *_rank(r, cuisines, by_cost)),
            )
            choice = next(
                (r for r in options if not _repeats(plan, day, slot, r.slug, relax)), None
            )
            if choice is None:
                return None
            plan[day, slot] = choice.slug
            needs_fast = needs_fast and not choice.fasting_ok
        if needs_fast:
            return None
    budget = h.weekly_budget_paise
    if budget is not None and "budget" not in relax:
        costs = {r.slug: r.cost_per_serving_paise for r in req.recipes}
        if sum(costs[s] * h.size for s in plan.values() if s) > budget:
            return None
    return plan


def plan_week(req: PlanRequest) -> WeekPlan:
    pairs = [frozenset(FALLBACK[i : i + 2]) for i in range(len(FALLBACK) - 1)]
    for relax in [frozenset(), *(frozenset({rule}) for rule in FALLBACK), *pairs]:
        for by_cost in (False, True):
            plan = _greedy(req, relax, by_cost)
            if plan is not None:
                return WeekPlan(
                    source="fallback",
                    status="feasible",
                    relaxed=[rule for rule in FALLBACK if rule in relax],
                    plan=[
                        PlannedMeal(day=d, slot=s, recipe=plan[d, s])
                        for d in range(7)
                        for s in SLOTS
                    ],
                    model=MODEL,
                )
    return WeekPlan(source="fallback", status="infeasible", relaxed=[], plan=None, model=MODEL)


# --- the pantry twin's closed form, without SciPy (M10 §3.3) -----------------------------------

TAP_FACTOR = {"ran_out": 1.3, "wasted": 0.7, "just_right": 1.0, None: 1.0}
Z80 = 1.2816  # the 80 % interval of a normal approximation to the predictive


def pantry_quantities(req: PantryRequest) -> PantryQuantities:
    """Gamma-Poisson update per item: the prior carries the cohort's weekly use with the weight
    of `prior_weeks`, each observed week adds the box's daily-use share (corrected by the tap)
    in steps. The interval is for next week's use: the posterior predictive is Negative Binomial
    (mean alpha/beta, variance alpha(beta+1)/beta^2 in steps); the ML service gives its exact
    percentiles, this gives the same mean with a normal interval, so a customer never waits on
    the ML service."""
    items = []
    for item in req.items:
        step = max(item.step, 1e-9)
        alpha = max(item.prior_weeks, 0.5) * max(item.prior_mean, 1e-9) / step
        beta = max(item.prior_weeks, 0.5)
        alpha += sum(o.qty * TAP_FACTOR[o.signal] / step for o in item.observations)
        beta += len(item.observations)
        mean = alpha / beta * step
        sd = math.sqrt(alpha * (beta + 1)) / beta * step
        items.append(
            PantryQuantity(
                product=item.product,
                mean=round(mean, 2),
                p10=round(max(0.0, mean - Z80 * sd), 2),
                p90=round(mean + Z80 * sd, 2),
                weeks_observed=len(item.observations),
                alpha=round(alpha, 4),
                beta=round(beta, 4),
            )
        )
    return PantryQuantities(source="fallback", model="pantry-rules-v0", items=items)
