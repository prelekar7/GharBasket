"""The weekly meal planner (M10 §3.1, handbook 08 M-C): CP-SAT, from spike S-4.

One choice per (day, slot): a recipe, or a tiffin for lunch and dinner on no-cook days. Hard rules
come first (`allowed` and the constraints in `solve`); among valid plans the solver maximises
3 x cuisine match + 5 x variety - 4 x distinct perishables bought (less waste).

`check()` re-verifies a plan independently of the model; the tests use it on every plan, and the
API's rule-based fallback is checked with it too.
"""

import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from ortools.sat.python import cp_model

from gharbasket_ml import nutrition
from gharbasket_ml.schemas import (
    HouseholdRules,
    PlannedMeal,
    PlanRequest,
    PlanResponse,
    RecipeIn,
    Rule,
    Score,
    Slot,
    Weights,
)

DAYS = range(7)
SLOTS: tuple[Slot, ...] = ("breakfast", "lunch", "dinner")
WEEKEND = (5, 6)
# No dish twice within this many days; lunch and dinner share their window.
WINDOWS = {"breakfast": 2, "main": 3}
WEIGHTS = {"preference": 3, "variety": 5, "perishables": 4}
# Comfort rules relaxed (in this order, one then two) when a household cannot be planned.
# Safety and religious rules (diet, Jain, diabetic, fasting) are never relaxed.
FALLBACK: tuple[Rule, ...] = ("repeat", "cook_time", "budget")

Plan = dict[tuple[int, Slot], str | None]
# The slots that share a no-repeat window, and the window's name in WINDOWS.
GROUPS: tuple[tuple[tuple[Slot, ...], str], ...] = (
    (("breakfast",), "breakfast"),
    (("lunch", "dinner"), "main"),
)


@dataclass(frozen=True)
class Week:
    household: HouseholdRules
    fasting: dict[int, bool]  # day → no onion or garlic that day
    recipes: dict[str, RecipeIn]  # the mains (sides are never planned as meals)
    sides: dict[str, RecipeIn] = field(default_factory=dict)
    weights: Weights = field(default_factory=Weights)

    @classmethod
    def of(cls, request: PlanRequest) -> "Week":
        fasting: dict[int, bool] = {}
        for f in request.fasting_days:
            fasting[f.day] = fasting.get(f.day, False) or f.no_onion_garlic
        return cls(
            request.household,
            fasting,
            {r.slug: r for r in request.recipes if not r.is_side},
            {r.slug: r for r in request.recipes if r.is_side},
            request.weights or Weights(),
        )

    def meal_nutrition(self, slug: str) -> tuple[float, float] | None:
        return nutrition.per_person(self.recipes[slug], self.sides)


def allowed(week: Week, r: RecipeIn, day: int, slot: Slot, relax: frozenset[Rule]) -> bool:
    """Hard rules that depend on one (day, slot, recipe) only."""
    h = week.household
    if slot not in r.slots:
        return False
    if (h.diet == "veg" and r.diet != "veg") or (h.diet == "egg" and r.diet == "nonveg"):
        return False
    if h.jain and not r.jain_ok:
        return False
    if h.diabetic and slot != "breakfast" and not r.diabetic_ok:
        return False
    if week.fasting.get(day) and not r.no_onion_garlic:
        return False
    limit = h.weekend_cook_minutes if day in WEEKEND else h.weekday_cook_minutes
    if "cook_time" not in relax and r.cook_minutes > limit:
        return False
    if day in h.no_cook_days:
        return slot == "breakfast" and r.no_cook
    return True


def is_tiffin(week: Week, day: int, slot: Slot) -> bool:
    return day in week.household.no_cook_days and slot != "breakfast"


def _windows(relax: frozenset[Rule]) -> dict[str, int]:
    # Relaxed, a dish may come back on a later day but never twice on the same day.
    return {"breakfast": 1, "main": 1} if "repeat" in relax else WINDOWS


def check(week: Week, plan: Plan, relax: frozenset[Rule] = frozenset()) -> list[str]:
    """Every hard rule, checked without the model. An empty list means the plan is valid."""
    problems: list[str] = []
    for day in DAYS:
        for slot in SLOTS:
            if (day, slot) not in plan:
                problems.append(f"day {day} {slot}: nothing planned")
                continue
            slug = plan[day, slot]
            if slug is None:
                if not is_tiffin(week, day, slot):
                    problems.append(f"day {day} {slot}: a tiffin on a cooking day")
            elif slug not in week.recipes or not allowed(
                week, week.recipes[slug], day, slot, relax
            ):
                problems.append(f"day {day} {slot}: {slug} breaks a rule")
        if day in week.fasting:
            today = [plan.get((day, s)) for s in SLOTS]
            if not any(s and week.recipes[s].fasting_ok for s in today if s in week.recipes):
                problems.append(f"day {day}: a fasting day without a fasting dish")
    windows = _windows(relax)
    for group, key in GROUPS:
        for start in DAYS:
            span = range(start, min(7, start + windows[key]))
            used = [plan.get((d, s)) for d in span for s in group]
            dishes = [u for u in used if u]
            if len(dishes) != len(set(dishes)):
                problems.append(
                    f"{key} from day {start}: a dish repeats within {windows[key]} days"
                )
    budget = week.household.weekly_budget_paise
    if budget is not None and "budget" not in relax and cost(week, plan) > budget:
        problems.append(f"over the budget: {cost(week, plan)} > {budget}")
    return problems


def cost(week: Week, plan: Plan) -> int:
    size = week.household.size
    return sum(week.recipes[s].cost_per_serving_paise * size for s in plan.values() if s)


def adherent_days(week: Week, plan: Plan) -> list[bool] | None:
    """Per day, whether the three meals' kcal and protein per person fall within ±20 % of the
    household's target (M10 §3.2); None when no dish carries nutrition data (a v0 request)."""
    if not any(week.meal_nutrition(s) for s in week.recipes):
        return None
    target = nutrition.target(week.household)
    days = []
    for day in DAYS:
        kcal = protein = 0.0
        for slot in SLOTS:
            slug = plan.get((day, slot))
            got = week.meal_nutrition(slug) if slug else None
            if got is not None:
                kcal, protein = kcal + got[0], protein + got[1]
        days.append(target.within(kcal, protein))
    return days


def price_penalty(week: Week, plan: Plan) -> float:
    return round(sum(week.recipes[s].price_spike or 0.0 for s in plan.values() if s), 3)


def score(week: Week, plan: Plan) -> Score:
    used = [week.recipes[s] for s in plan.values() if s]
    cuisines = set(week.household.cuisines)
    days = adherent_days(week, plan)
    return Score(
        preference=sum(r.cuisine in cuisines for r in used),
        variety=len({r.slug for r in used}),
        perishables=len({p for r in used for p in r.perishables}),
        cost_paise=cost(week, plan),
        nutrition=None if days is None else round(sum(days) / len(days), 3),
        price_penalty=price_penalty(week, plan) if any(r.price_spike for r in used) else None,
        liking=(
            round(sum(r.preference or 0.0 for r in used) / len(used), 3)
            if used and any(r.preference is not None for r in used)
            else None
        ),
    )


@dataclass(frozen=True)
class Solved:
    status: str  # "optimal", "feasible" or "infeasible"
    plan: Plan | None


def solve(
    week: Week,
    time_limit_s: float,
    relax: frozenset[Rule] = frozenset(),
    *,
    deterministic: bool = False,
) -> Solved:
    m = cp_model.CpModel()
    x: dict[tuple[int, Slot, str], cp_model.IntVar] = {}
    for day in DAYS:
        for slot in SLOTS:
            choices = []
            for r in week.recipes.values():
                if allowed(week, r, day, slot, relax):
                    x[day, slot, r.slug] = m.new_bool_var(f"x_{day}_{slot}_{r.slug}")
                    choices.append(x[day, slot, r.slug])
            if is_tiffin(week, day, slot):
                choices.append(m.new_bool_var(f"tiffin_{day}_{slot}"))
            m.add_exactly_one(choices)

    windows = _windows(relax)
    for slug in week.recipes:
        for group, key in GROUPS:
            for start in DAYS:
                span = range(start, min(7, start + windows[key]))
                window = [x[d, s, slug] for d in span for s in group if (d, s, slug) in x]
                if len(window) > 1:
                    m.add(sum(window) <= 1)
    for day in week.fasting:
        fasting = [v for (d, _, slug), v in x.items() if d == day and week.recipes[slug].fasting_ok]
        m.add(sum(fasting) >= 1)
    budget = week.household.weekly_budget_paise
    if budget is not None and "budget" not in relax:
        size = week.household.size
        m.add(
            sum(
                week.recipes[slug].cost_per_serving_paise * size * v
                for (_, _, slug), v in x.items()
            )
            <= budget
        )

    cuisines = set(week.household.cuisines)
    preference = sum(v for (_, _, slug), v in x.items() if week.recipes[slug].cuisine in cuisines)
    used = {
        slug: _any(m, [v for (_, _, s), v in x.items() if s == slug], f"used_{slug}")
        for slug in week.recipes
    }
    perishables = {p for r in week.recipes.values() for p in r.perishables}
    bought = [
        _any(m, [used[r.slug] for r in week.recipes.values() if p in r.perishables], f"buy_{p}")
        for p in sorted(perishables)
    ]
    w = week.weights
    # v2: the learned liking; a dish the household always kept is worth liking x 10 points,
    # about two dishes of variety, so taste tilts the plan without overriding the rules.
    liking = sum(
        round(10 * (week.recipes[slug].preference or 0.0)) * v for (_, _, slug), v in x.items()
    )
    objective = (
        w.preference * preference
        + w.liking * liking
        + w.variety * sum(used.values())
        - w.perishables * sum(bought)
        - w.price
        * sum(
            round(100 * (week.recipes[slug].price_spike or 0)) * v for (_, _, slug), v in x.items()
        )
    )
    # v1 nutrition: a day counts when its meals' kcal and protein per person are within ±20 %
    # of the target; linearised with a big M (an indicator per day and nutrient bound).
    nourished = {slug: week.meal_nutrition(slug) for slug in week.recipes}
    if any(nourished.values()):
        target = nutrition.target(week.household)
        big = 100_000
        for day in DAYS:
            ok = m.new_bool_var(f"nourished_{day}")
            meals = [
                (got, v) for (d, _, slug), v in x.items() if d == day and (got := nourished[slug])
            ]
            kcal = sum(round(got[0]) * v for got, v in meals)
            protein = sum(round(10 * got[1]) * v for got, v in meals)
            for total, low, high in (
                (
                    kcal,
                    (1 - nutrition.TOLERANCE) * target.kcal,
                    (1 + nutrition.TOLERANCE) * target.kcal,
                ),
                (
                    protein,
                    10 * (1 - nutrition.TOLERANCE) * target.protein_g,
                    10 * (1 + nutrition.TOLERANCE) * target.protein_g,
                ),
            ):
                m.add(total >= round(low) - big * (1 - ok))
                m.add(total <= round(high) + big * (1 - ok))
            objective += (
                100 * w.nutrition * ok
            )  # one adherent day ≈ a hundred points of variety-scale
    m.maximize(objective)

    solver = cp_model.CpSolver()
    solver.parameters.num_workers = 8
    solver.parameters.random_seed = 7
    if deterministic:
        # The same plan on every run and machine (the simulator needs this): workers take turns
        # in fixed batches, and the limit counts work done rather than wall-clock seconds.
        solver.parameters.interleave_search = True
        solver.parameters.max_deterministic_time = time_limit_s
    else:
        # Several plans can tie for the best score; parallel workers may return any of them.
        solver.parameters.max_time_in_seconds = time_limit_s
    status = solver.solve(m)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return Solved("infeasible", None)
    plan: Plan = {}
    for day in DAYS:
        for slot in SLOTS:
            plan[day, slot] = next(
                (
                    slug
                    for (d, s, slug), v in x.items()
                    if d == day and s == slot and solver.value(v)
                ),
                None,
            )
    return Solved("optimal" if status == cp_model.OPTIMAL else "feasible", plan)


def _any(m: cp_model.CpModel, literals: Iterable[cp_model.IntVar], name: str) -> cp_model.IntVar:
    """A bool that is true when any of `literals` is (false when there are none)."""
    flag = m.new_bool_var(name)
    items = list(literals)
    if items:
        m.add_max_equality(flag, items)
    else:
        m.add(flag == 0)
    return flag


def meal(week: Week, plan: Plan, day: int, slot: Slot, relax: frozenset[Rule]) -> PlannedMeal:
    """A planned meal with its sides and reason codes (M10 §3.2)."""
    slug = plan[day, slot]
    reasons: list[Any] = []
    if slug is None:
        reasons.append("TIFFIN")
    if slug is not None and day in week.fasting:
        reasons.append("FESTIVAL")
    if slug is not None and "budget" in relax:
        reasons.append("BUDGET")
    return PlannedMeal(
        day=day,
        slot=slot,
        recipe=slug,
        sides=list(week.recipes[slug].sides) if slug else [],
        reasons=reasons,
    )


def relaxations() -> list[frozenset[Rule]]:
    """No relaxation, then each comfort rule alone, then adjacent pairs, in FALLBACK order."""
    pairs = [frozenset(FALLBACK[i : i + 2]) for i in range(len(FALLBACK) - 1)]
    return [frozenset(), *(frozenset({rule}) for rule in FALLBACK), *pairs]


def plan_week(request: PlanRequest, *, deterministic: bool = False) -> PlanResponse:
    """`deterministic` (in-process callers only, e.g. the simulator) returns the same plan every
    run, at some cost in speed; the HTTP endpoint keeps the faster default."""
    week = Week.of(request)
    started = time.perf_counter()
    for relax in relaxations():
        solved = solve(week, request.time_limit_s, relax, deterministic=deterministic)
        if solved.plan is not None:
            return PlanResponse(
                status=solved.status,  # pyright: ignore[reportArgumentType]
                relaxed=[rule for rule in FALLBACK if rule in relax],
                plan=[meal(week, solved.plan, d, s, relax) for d in DAYS for s in SLOTS],
                score=score(week, solved.plan),
                seconds=round(time.perf_counter() - started, 4),
            )
    return PlanResponse(
        status="infeasible",
        relaxed=[],
        plan=None,
        score=None,
        seconds=round(time.perf_counter() - started, 4),
    )
