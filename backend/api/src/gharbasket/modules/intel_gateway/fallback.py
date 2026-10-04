"""The rule-based weekly planner: what customers get when the ML service is slow, down or unset.

Same hard rules as the CP-SAT planner (M10 §3.1) and the same comfort-rule relaxations, but
greedy: day by day, it takes the best allowed dish (household cuisine first, then cheapest) that
keeps the no-repeat windows, placing a fasting dish first on fasting days. Valid, not optimal.
"""

import math

from gharbasket.modules.intel_gateway.schemas import (
    PantryQuantities,
    PantryQuantity,
    PantryRequest,
    PlannedMeal,
    PlanRecipe,
    PlanRequest,
    Reason,
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


def _allowed(req: PlanRequest, r: PlanRecipe, day: int, slot: Slot, relax: frozenset[Rule]) -> bool:
    h = req.household
    if slot not in r.slots:
        return False
    if (h.diet == "veg" and r.diet != "veg") or (h.diet == "egg" and r.diet == "nonveg"):
        return False
    if (h.jain and not r.jain_ok) or (h.diabetic and slot != "breakfast" and not r.diabetic_ok):
        return False
    if any(f.day == day and f.no_onion_garlic for f in req.fasting_days) and not r.no_onion_garlic:
        return False
    limit = h.weekend_cook_minutes if day in WEEKEND else h.weekday_cook_minutes
    if "cook_time" not in relax and r.cook_minutes > limit:
        return False
    if day in h.no_cook_days:
        return slot == "breakfast" and r.no_cook
    return True


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

    def rank(r: PlanRecipe) -> tuple[bool, int, str]:
        """Household cuisine first (unless only cost counts), then cheapest, then by name."""
        return (not by_cost and r.cuisine not in cuisines, r.cost_per_serving_paise, r.slug)

    plan: Plan = {}
    for day in range(7):
        needs_fast = day in fasting_days
        for slot in SLOTS:
            if day in h.no_cook_days and slot != "breakfast":
                plan[day, slot] = None
                continue
            options = sorted(
                (r for r in req.recipes if not r.is_side and _allowed(req, r, day, slot, relax)),
                key=lambda r: (needs_fast and not r.fasting_ok, *rank(r)),
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


def _reasons(slug: str | None) -> list[Reason]:
    return ["FALLBACK", "TIFFIN"] if slug is None else ["FALLBACK"]


def plan_week(req: PlanRequest) -> WeekPlan:
    sides = {r.slug: r.sides for r in req.recipes}
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
                        PlannedMeal(
                            day=d,
                            slot=s,
                            recipe=plan[d, s],
                            sides=list(sides.get(plan[d, s] or "", [])),
                            reasons=_reasons(plan[d, s]),
                        )
                        for d in range(7)
                        for s in SLOTS
                    ],
                    model=MODEL,
                )
    return WeekPlan(source="fallback", status="infeasible", relaxed=[], plan=None, model=MODEL)


# --- the pantry twin's closed form, without SciPy (M10 §3.3) -----------------------------------

TAP_FACTOR = {"ran_out": 1.3, "wasted": 0.7, "just_right": 1.0, None: 1.0}
Z80 = 1.2816  # the 80 % interval of a normal approximation to the Gamma posterior


def pantry_quantities(req: PantryRequest) -> PantryQuantities:
    """Gamma-Poisson update per item: the prior carries the cohort's weekly use with the weight
    of `prior_weeks`, each observed week adds the box's quantity (corrected by the tap) in steps.
    The ML service gives the same mean with exact Gamma percentiles; this is the same mean with
    a normal interval, so a customer never waits on the ML service."""
    items = []
    for item in req.items:
        step = max(item.step, 1e-9)
        alpha = max(item.prior_weeks, 0.5) * max(item.prior_mean, 1e-9) / step
        beta = max(item.prior_weeks, 0.5)
        alpha += sum(o.qty * TAP_FACTOR[o.signal] / step for o in item.observations)
        beta += len(item.observations)
        mean = alpha / beta * step
        sd = math.sqrt(alpha) / beta * step
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
