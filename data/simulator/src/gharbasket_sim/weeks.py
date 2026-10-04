"""A household's weeks: the plan arrives, the household accepts or edits each meal (or skips the
week's box), and eats. Pure and deterministic for a given seed.

Behaviour (ground truth, so models can be scored against it):
- a planned dish is accepted with probability sigmoid(accept_bias + taste);
- otherwise it is swapped for the household's favourite allowed dish (taste plus Gumbel noise),
  keeping every rule the planner kept: diet, Jain, diabetic, fasting, cook time;
- a skipped week means no box was ordered; the family still cooks and eats the same way;
- consumption = the cooked dishes' ingredients x adult equivalents, plus daily-use items.
"""

import json
import math
import random
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from gharbasket_ml import planner as cpsat
from gharbasket_ml.schemas import PlanRequest

from gharbasket.modules.intel_gateway import fallback
from gharbasket.modules.intel_gateway.schemas import PlanRequest as GatewayRequest
from gharbasket_sim.catalog import Catalog, Festival
from gharbasket_sim.households import SimHousehold

SLOTS = ("breakfast", "lunch", "dinner")
TIME_LIMIT_S = 2.0


@dataclass(frozen=True)
class Planned:
    status: str
    relaxed: tuple[str, ...]
    meals: dict[tuple[int, str], str | None]  # empty when infeasible
    model: str


Planner = Callable[[dict[str, Any]], Planned]


def _planned(answer: Any) -> Planned:
    meals = {(m.day, m.slot): m.recipe for m in answer.plan or []}
    return Planned(answer.status, tuple(answer.relaxed), meals, answer.model)


def cpsat_planner(request: dict[str, Any]) -> Planned:
    """The M10 CP-SAT planner, in process and in its deterministic mode."""
    return _planned(cpsat.plan_week(PlanRequest.model_validate(request), deterministic=True))


def rules_planner(request: dict[str, Any]) -> Planned:
    """What customers get when the ML service is down (intel_gateway's fallback)."""
    return _planned(fallback.plan_week(GatewayRequest.model_validate(request)))


PLANNERS: dict[str, Planner] = {"cpsat": cpsat_planner, "rules": rules_planner}


def cached(planner: Planner) -> Planner:
    """Households with the same rules in the same week get the same plan: solve it once."""
    seen: dict[str, Planned] = {}

    def plan(request: dict[str, Any]) -> Planned:
        key = json.dumps(request, sort_keys=True)
        if key not in seen:
            seen[key] = planner(request)
        return seen[key]

    return plan


def fasting_days(h: SimHousehold, monday: date, festivals: list[Festival]) -> list[dict[str, Any]]:
    days: dict[int, bool] = {}
    for d in range(7):
        day = monday + timedelta(days=d)
        if "tuesdays" in h.fasts and d == 1:
            days[d] = h.no_onion_garlic_on_fasts
        for f in festivals:
            if f.day == day and f.code in h.fasts and f.rules.get("fasting"):
                strict = h.no_onion_garlic_on_fasts or f.rules.get("no_onion_garlic", False)
                days[d] = days.get(d, False) or strict
    return [{"day": d, "no_onion_garlic": strict} for d, strict in sorted(days.items())]


@dataclass
class Rows:
    weeks: list[dict[str, Any]]
    meals: list[dict[str, Any]]
    consumption: list[dict[str, Any]]


def simulate(
    seed: int,
    h: SimHousehold,
    catalog: Catalog,
    first_monday: date,
    weeks: int,
    planner: Planner,
) -> Rows:
    rows = Rows([], [], [])
    for w in range(weeks):
        rng = random.Random(f"gharbasket-sim:{seed}:{h.key}:{w}")
        monday = first_monday + timedelta(weeks=w)
        request = {
            "household": h.rules,
            "fasting_days": fasting_days(h, monday, catalog.festivals),
            "recipes": catalog.recipes,
            "time_limit_s": TIME_LIMIT_S,
        }
        planned = planner(request)
        ordered = rng.random() >= h.skip_week
        rows.weeks.append(
            {
                "household": h.key,
                "week": w,
                "monday": monday,
                "ordered": ordered,
                "plan_status": planned.status,
                "relaxed": list(planned.relaxed),
                "planner": planned.model,
                "fasting_days": len(request["fasting_days"]),
            }
        )
        cooked = _week_of_meals(rng, h, request, planned, w, monday, rows.meals)
        used: dict[str, float] = defaultdict(float)
        for slug in cooked:
            for i in catalog.ingredients[slug]:
                if not i.optional or rng.random() < 0.5:
                    used[i.product] += i.qty * h.adult_equivalents
        for slug, weekly in h.daily_use.items():
            used[slug] += weekly * math.exp(rng.gauss(0, 0.1))
        for slug in sorted(used):
            rows.consumption.append(
                {
                    "household": h.key,
                    "week": w,
                    "product": slug,
                    "unit": catalog.products[slug].unit,
                    "qty": round(used[slug], 1),
                }
            )
    return rows


def _week_of_meals(
    rng: random.Random,
    h: SimHousehold,
    request: dict[str, Any],
    planned: Planned,
    w: int,
    monday: date,
    out: list[dict[str, Any]],
) -> list[str]:
    week = cpsat.Week.of(PlanRequest.model_validate(request))
    # Without a plan the family cooks for itself, under the most relaxed comfort rules.
    relax = frozenset(planned.relaxed if planned.meals else cpsat.FALLBACK)
    cooked: list[str] = []
    for day in range(7):
        today: set[str] = set()
        for slot in SLOTS:
            dish = planned.meals.get((day, slot))
            if planned.meals and dish is None:
                action, final = "tiffin", None
            elif dish is not None and rng.random() < _sigmoid(h.accept_bias + h.taste[dish]):
                action, final = "accept", dish
            else:
                must_fast = (
                    dish is not None and week.recipes[dish].fasting_ok and day in week.fasting
                )
                final = _favourite(rng, h, week, day, slot, relax, today | {dish or ""}, must_fast)
                action = "edit" if dish is not None else "own"
                if final is None:  # nothing else is allowed: keep the plan (or go without)
                    action, final = ("accept", dish) if dish else ("none", None)
            if final:
                today.add(final)
                cooked.append(final)
            out.append(
                {
                    "household": h.key,
                    "week": w,
                    "date": monday + timedelta(days=day),
                    "slot": slot,
                    "planned": dish,
                    "action": action,
                    "final": final,
                }
            )
    return cooked


def _favourite(
    rng: random.Random,
    h: SimHousehold,
    week: cpsat.Week,
    day: int,
    slot: str,
    relax: frozenset[Any],
    exclude: set[str],
    must_fast: bool,
) -> str | None:
    best, best_score = None, -math.inf
    for slug, recipe in sorted(week.recipes.items()):
        if slug in exclude or (must_fast and not recipe.fasting_ok):
            continue
        if not cpsat.allowed(week, recipe, day, slot, relax):  # pyright: ignore[reportArgumentType]
            continue
        score = h.taste[slug] - math.log(-math.log(rng.random() or 1e-12))  # + Gumbel noise
        if score > best_score:
            best, best_score = slug, score
    return best


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))
