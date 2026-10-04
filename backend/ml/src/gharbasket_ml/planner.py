"""The weekly meal planner (M10 §3.1, handbook 08 M-C): CP-SAT, from spike S-4.

One choice per (day, slot): a recipe, or a tiffin for lunch and dinner on no-cook days. Hard rules
come first (`allowed` and the constraints in `solve`); among valid plans the solver maximises
3 x cuisine match + 5 x variety - 4 x distinct perishables bought (less waste).

`check()` re-verifies a plan independently of the model; the tests use it on every plan, and the
API's rule-based fallback is checked with it too.
"""

import time
from collections.abc import Iterable
from dataclasses import dataclass

from ortools.sat.python import cp_model

from gharbasket_ml.schemas import (
    HouseholdRules,
    PlannedMeal,
    PlanRequest,
    PlanResponse,
    RecipeIn,
    Rule,
    Score,
    Slot,
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
    recipes: dict[str, RecipeIn]

    @classmethod
    def of(cls, request: PlanRequest) -> "Week":
        fasting: dict[int, bool] = {}
        for f in request.fasting_days:
            fasting[f.day] = fasting.get(f.day, False) or f.no_onion_garlic
        return cls(request.household, fasting, {r.slug: r for r in request.recipes})


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


def score(week: Week, plan: Plan) -> Score:
    used = [week.recipes[s] for s in plan.values() if s]
    cuisines = set(week.household.cuisines)
    return Score(
        preference=sum(r.cuisine in cuisines for r in used),
        variety=len({r.slug for r in used}),
        perishables=len({p for r in used for p in r.perishables}),
        cost_paise=cost(week, plan),
    )


@dataclass(frozen=True)
class Solved:
    status: str  # "optimal", "feasible" or "infeasible"
    plan: Plan | None


def solve(week: Week, time_limit_s: float, relax: frozenset[Rule] = frozenset()) -> Solved:
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
    m.maximize(
        WEIGHTS["preference"] * preference
        + WEIGHTS["variety"] * sum(used.values())
        - WEIGHTS["perishables"] * sum(bought)
    )

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit_s
    solver.parameters.num_workers = 8
    solver.parameters.random_seed = 7
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


def relaxations() -> list[frozenset[Rule]]:
    """No relaxation, then each comfort rule alone, then adjacent pairs, in FALLBACK order."""
    pairs = [frozenset(FALLBACK[i : i + 2]) for i in range(len(FALLBACK) - 1)]
    return [frozenset(), *(frozenset({rule}) for rule in FALLBACK), *pairs]


def plan_week(request: PlanRequest) -> PlanResponse:
    week = Week.of(request)
    started = time.perf_counter()
    for relax in relaxations():
        solved = solve(week, request.time_limit_s, relax)
        if solved.plan is not None:
            return PlanResponse(
                status=solved.status,  # pyright: ignore[reportArgumentType]
                relaxed=[rule for rule in FALLBACK if rule in relax],
                plan=[
                    PlannedMeal(day=d, slot=s, recipe=solved.plan[d, s])
                    for d in DAYS
                    for s in SLOTS
                ],
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
