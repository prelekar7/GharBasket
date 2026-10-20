"""The weekly meal planner (M10 §3.1, handbook 08 M-C): CP-SAT, from spike S-4.

One choice per (day, slot): a recipe, or a tiffin for lunch and dinner on no-cook days. Hard rules
come first (`allowed` and the constraints in `solve`); among valid plans the solver maximises
3 x cuisine match + 5 x variety - 4 x distinct perishables bought (less waste), in v1 (§3.2)
+ 4 x adherent days - 3 x the chosen dishes' price spikes, and in v2 + 3 x the chosen dishes'
liking (0..1 per meal), every term on one scale (`SCALE`).

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


def adherent_days(week: Week, plan: Plan) -> dict[int, bool] | None:
    """Per scored day (no-cook days are left out, see `scored_days`), whether the three meals'
    kcal and protein per person fall within ±20 % of the household's target (M10 §3.2); None
    when no dish carries nutrition data (a v0 request)."""
    if not any(week.meal_nutrition(s) for s in week.recipes):
        return None
    target = nutrition.target(week.household)
    days: dict[int, bool] = {}
    for day in scored_days(week):
        kcal = protein = 0.0
        for slot in SLOTS:
            slug = plan.get((day, slot))
            got = week.meal_nutrition(slug) if slug else None
            if got is not None:
                kcal, protein = kcal + got[0], protein + got[1]
        days[day] = target.within(kcal, protein)
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
        nutrition=round(sum(days.values()) / len(days), 3) if days else None,
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


# Every objective term is counted in hundredths of a weight point, so that a price spike (0.62)
# stays an integer and costs its share of the weight: one cuisine match is worth
# SCALE x preference, one adherent day SCALE x nutrition, a dish 62 % above its index
# 0.62 x SCALE x price, a meal of a dish liked 0.8 (v2) 0.8 x SCALE x liking.
SCALE = 100
# v1 warm start: this share of the time limit goes to the v0 objective (no nutrition term), whose
# plan then seeds the v1 search as a hint; the rest goes to the v1 objective.
WARM_START_SHARE = 0.3
# The least a solve is given, even when an earlier relaxation used up the request's time limit.
MIN_SOLVE_S = 0.05


def scored_days(week: Week) -> list[int]:
    """The days the nutrition term counts: a no-cook day's tiffins carry no nutrition data, so
    those days are left out of the score and its denominator (M10 §3.2)."""
    return [d for d in DAYS if d not in week.household.no_cook_days]


@dataclass(frozen=True)
class _Bound:
    """One day's nutrient total within [low, high], in integer units (kcal, decigrams protein)."""

    terms: list[tuple[int, cp_model.IntVar]]
    low: int
    high: int


def _nutrition_bounds(
    week: Week, x: dict[tuple[int, Slot, str], cp_model.IntVar]
) -> dict[int, list[_Bound]]:
    """Per scored day, the kcal and protein bounds a day must meet to count as adherent; empty
    when no dish carries nutrition data (a v0 request)."""
    nourished = {slug: week.meal_nutrition(slug) for slug in week.recipes}
    if not any(nourished.values()):
        return {}
    target = nutrition.target(week.household)
    low, high = 1 - nutrition.TOLERANCE, 1 + nutrition.TOLERANCE
    bounds: dict[int, list[_Bound]] = {}
    for day in scored_days(week):
        meals = [(got, v) for (d, _, slug), v in x.items() if d == day and (got := nourished[slug])]
        bounds[day] = [
            _Bound(
                [(round(got[0]), v) for got, v in meals],
                round(low * target.kcal),
                round(high * target.kcal),
            ),
            _Bound(
                [(round(10 * got[1]), v) for got, v in meals],
                round(10 * low * target.protein_g),
                round(10 * high * target.protein_g),
            ),
        ]
    return bounds


def _solver(limit_s: float, deterministic: bool) -> cp_model.CpSolver:
    solver = cp_model.CpSolver()
    solver.parameters.num_workers = 8
    solver.parameters.random_seed = 7
    if deterministic:
        # The same plan on every run and machine (the simulator needs this): workers take turns
        # in fixed batches, and the limit counts work done rather than wall-clock seconds.
        solver.parameters.interleave_search = True
        solver.parameters.max_deterministic_time = limit_s
    else:
        # Several plans can tie for the best score; parallel workers may return any of them.
        solver.parameters.max_time_in_seconds = limit_s
    return solver


def _plan_of(solver: cp_model.CpSolver, x: dict[tuple[int, Slot, str], cp_model.IntVar]) -> Plan:
    plan: Plan = {(day, slot): None for day in DAYS for slot in SLOTS}
    for (day, slot, slug), v in x.items():
        if solver.value(v):
            plan[day, slot] = slug
    return plan


def solve(
    week: Week,
    time_limit_s: float,
    relax: frozenset[Rule] = frozenset(),
    *,
    deterministic: bool = False,
) -> Solved:
    started = time.perf_counter()
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
    # v2: the learned liking, on the same scale: each meal of a fully liked dish is worth
    # liking x SCALE, one cuisine match at the default weights (3 = 3), 0.6 of a distinct dish
    # of variety (5), so taste tilts the plan without overriding any rule or the variety.
    liking = sum(
        round(SCALE * (week.recipes[slug].preference or 0.0)) * v for (_, _, slug), v in x.items()
    )
    v0_objective = (
        SCALE
        * (w.preference * preference + w.variety * sum(used.values()) - w.perishables * sum(bought))
        + w.liking * liking
        - w.price
        * sum(
            round(SCALE * (week.recipes[slug].price_spike or 0)) * v
            for (_, _, slug), v in x.items()
        )
    )
    # v1 nutrition: a scored day counts when its meals' kcal and protein per person are within
    # ±20 % of the target; one indicator per day enforces the four bounds when it is set.
    bounds = _nutrition_bounds(week, x) if w.nutrition else {}
    adherent: dict[int, cp_model.IntVar] = {}
    for day, day_bounds in bounds.items():
        adherent[day] = m.new_bool_var(f"adherent_{day}")
        for b in day_bounds:
            total = sum(c * v for c, v in b.terms)
            m.add(total >= b.low).only_enforce_if(adherent[day])
            m.add(total <= b.high).only_enforce_if(adherent[day])

    m.maximize(v0_objective)
    if not adherent:
        status = (solver := _solver(time_limit_s, deterministic)).solve(m)
        if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            return Solved("infeasible", None)
        return Solved("optimal" if status == cp_model.OPTIMAL else "feasible", _plan_of(solver, x))

    # v1: the indicators make a cold search slow to prove optimal, so a quick v0 solve (same hard
    # rules, no nutrition term) gives a valid plan first and seeds the v1 search as a hint.
    first = _solver(WARM_START_SHARE * time_limit_s, deterministic)
    status = first.solve(m)
    if status == cp_model.INFEASIBLE:
        return Solved("infeasible", None)
    warm: Plan | None = None
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        warm = _plan_of(first, x)
        for v in [*x.values(), *used.values(), *bought]:
            m.add_hint(v, first.value(v))
        for day, ok in adherent.items():
            m.add_hint(
                ok,
                all(
                    b.low <= sum(c * first.value(v) for c, v in b.terms) <= b.high
                    for b in bounds[day]
                ),
            )
    m.maximize(v0_objective + SCALE * w.nutrition * sum(adherent.values()))
    spent = time.perf_counter() - started
    remaining = (1 - WARM_START_SHARE) * time_limit_s if deterministic else time_limit_s - spent
    second = _solver(max(remaining, MIN_SOLVE_S), deterministic)
    status = second.solve(m)
    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return Solved("optimal" if status == cp_model.OPTIMAL else "feasible", _plan_of(second, x))
    if warm is not None:  # out of time before the hint was even reloaded: keep the v0 plan
        return Solved("feasible", warm)
    return Solved("infeasible", None)


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
        # `time_limit_s` bounds the whole request, so the caller's timeout holds: an infeasible
        # relaxation is usually proven in milliseconds, and the next one gets what is left.
        # Deterministic mode counts work rather than seconds and gives each relaxation the limit.
        left = request.time_limit_s - (time.perf_counter() - started)
        limit = request.time_limit_s if deterministic else max(left, MIN_SOLVE_S)
        solved = solve(week, limit, relax, deterministic=deterministic)
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
