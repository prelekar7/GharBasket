"""A household's weeks: the plan arrives, the household accepts or edits each meal (or skips the
week's box), and eats. Pure and deterministic for a given seed.

Behaviour (ground truth, so models can be scored against it):
- a planned dish is accepted with probability sigmoid(accept_bias + taste);
- otherwise it is swapped for the household's favourite allowed dish (taste plus Gumbel noise),
  keeping every rule the planner kept: diet, Jain, diabetic, fasting, cook time;
- a skipped week means no box was ordered; the family still cooks and eats the same way;
- consumption = the cooked dishes' ingredients x adult equivalents, plus daily-use items;
- v1a: the week's box is drafted like M3's, edited by the household (pantry, taste, prices),
  delivered into a pantry that the week's cooking draws down, with ran-out / wasted taps
  (`pantry.py`); prices follow the shock episodes in shocks.json.
"""

import json
import math
import random
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from gharbasket_ml import planner as cpsat
from gharbasket_ml.schemas import PlanRequest

from gharbasket.modules.intel_gateway import fallback
from gharbasket.modules.intel_gateway.schemas import PlanRequest as GatewayRequest
from gharbasket_sim import pantry as pantry_sim
from gharbasket_sim.catalog import Catalog, Festival
from gharbasket_sim.config import Shocks
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


def without_preference(request: dict[str, Any]) -> dict[str, Any]:
    """The v1 request: the observed liking stripped, so the plan ignores it."""
    return {
        **request,
        "recipes": [{k: v for k, v in r.items() if k != "preference"} for r in request["recipes"]],
    }


def cpsat_planner(request: dict[str, Any]) -> Planned:
    """The M10 CP-SAT planner v1, in process and in its deterministic mode (no liking term)."""
    answer = cpsat.plan_week(
        PlanRequest.model_validate(without_preference(request)), deterministic=True
    )
    return _planned(answer)


def cpsat_v2_planner(request: dict[str, Any]) -> Planned:
    """Planner v2: the same model with the household's observed liking of each dish (the share
    of past weeks it accepted the dish when planned) in the objective."""
    return _planned(cpsat.plan_week(PlanRequest.model_validate(request), deterministic=True))


def rules_planner(request: dict[str, Any]) -> Planned:
    """What customers get when the ML service is down (intel_gateway's fallback)."""
    return _planned(fallback.plan_week(GatewayRequest.model_validate(request)))


def _slot_by_slot(request: dict[str, Any], choose: Callable[..., str | None], name: str) -> Planned:
    """A baseline that fills the week one slot at a time under the same hard rules as the CP-SAT
    model (no dish twice in its window), relaxing the comfort rules in the same order only when a
    slot has no allowed dish at all."""
    week = cpsat.Week.of(PlanRequest.model_validate(request))
    for relax in cpsat.relaxations():
        meals: dict[tuple[int, str], str | None] = {}
        recent: dict[str, list[tuple[int, str]]] = {}
        feasible = True
        for day in range(7):
            for slot in SLOTS:
                if cpsat.is_tiffin(week, day, slot):
                    meals[(day, slot)] = None
                    continue
                window = cpsat.WINDOWS["breakfast" if slot == "breakfast" else "main"]
                if "repeat" in relax:
                    window = 1
                options = [
                    r
                    for r in week.recipes.values()
                    if cpsat.allowed(week, r, day, slot, relax)  # pyright: ignore[reportArgumentType]
                    and all(
                        day - d >= window or (d == day and s != slot and False)
                        for d, s in recent.get(r.slug, [])
                    )
                ]
                chosen = choose(week, options, day, slot)
                if chosen is None:
                    feasible = False
                    break
                meals[(day, slot)] = chosen
                recent.setdefault(chosen, []).append((day, slot))
            if not feasible:
                break
        if feasible:
            return Planned("feasible", tuple(sorted(relax)), meals, name)
    return Planned("infeasible", (), {}, name)


def greedy_planner(request: dict[str, Any]) -> Planned:
    """Baseline: the cheapest allowed dish for every slot (a household's own cuisine first)."""

    def choose(week: Any, options: list[Any], day: int, slot: str) -> str | None:
        if not options:
            return None
        own = set(week.household.cuisines)
        return min(
            options, key=lambda r: (r.cuisine not in own, r.cost_per_serving_paise, r.slug)
        ).slug

    return _slot_by_slot(request, choose, "greedy")


def random_planner(request: dict[str, Any]) -> Planned:
    """Baseline: a random valid plan (seeded by the request, so it is reproducible)."""
    rng = random.Random(json.dumps(request, sort_keys=True))

    def choose(week: Any, options: list[Any], day: int, slot: str) -> str | None:
        return None if not options else rng.choice(sorted(r.slug for r in options))

    return _slot_by_slot(request, choose, "random")


PLANNERS: dict[str, Planner] = {
    "cpsat": cpsat_planner,
    "cpsat_v2": cpsat_v2_planner,
    "rules": rules_planner,
    "greedy": greedy_planner,
    "random": random_planner,
}


def cached(planner: Planner) -> Planner:
    """Households with the same rules in the same week get the same plan: solve it once."""
    seen: dict[str, Planned] = {}

    def plan(request: dict[str, Any]) -> Planned:
        key = json.dumps(request, sort_keys=True)
        if key not in seen:
            seen[key] = planner(request)
        return seen[key]

    return plan


MIN_OBSERVED = 2  # weeks of history before a dish's liking counts


def observed_preference(meals: list[dict[str, Any]], household: str) -> dict[str, float]:
    """What M3 learns from the ranker, here from behaviour: per planned dish, the share of
    times the household accepted it (0..1), once it has at least two weeks of history."""
    mine = [m for m in meals if m["household"] == household and m["planned"]]
    if len({m["week"] for m in mine}) < MIN_OBSERVED:
        return {}
    seen: dict[str, list[int]] = {}
    for m in mine:
        seen.setdefault(m["planned"], []).append(int(m["action"] == "accept"))
    return {slug: round(sum(hits) / len(hits), 3) for slug, hits in seen.items()}


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
    boxes: list[dict[str, Any]] = field(default_factory=list)
    box_lines: list[dict[str, Any]] = field(default_factory=list)
    edits: list[dict[str, Any]] = field(default_factory=list)
    pantry: list[dict[str, Any]] = field(default_factory=list)
    outcomes: list[dict[str, Any]] = field(default_factory=list)  # --write-db only


@dataclass(frozen=True)
class Provided:
    """Plans and drafted boxes that came from M3 (`--write-db`), one per week, in place of the
    in-process planner and the simulator's own draft."""

    plans: list[Planned]
    drafts: list[list[pantry_sim.Line]]


def simulate(
    seed: int,
    h: SimHousehold,
    catalog: Catalog,
    first_monday: date,
    weeks: int,
    planner: Planner,
    shocks: Shocks | None = None,
    *,
    price_sensitivity: float = 0.5,
    feedback_rate: float = 0.4,
    provided: Provided | None = None,
) -> Rows:
    rows = Rows([], [], [])
    pantry = pantry_sim.initial_stock(random.Random(f"gharbasket-sim:{seed}:{h.key}"), h, catalog)
    for w in range(weeks):
        rng = random.Random(f"gharbasket-sim:{seed}:{h.key}:{w}")
        monday = first_monday + timedelta(weeks=w)
        factor = pantry_sim.weekly_prices(catalog, shocks, w)
        liked = observed_preference(rows.meals, h.key)
        request = {
            "household": h.rules,
            "fasting_days": fasting_days(h, monday, catalog.festivals),
            "recipes": [
                {**r, "preference": liked[r["slug"]]} if r["slug"] in liked else r
                for r in catalog.recipes
            ],
            "time_limit_s": TIME_LIMIT_S,
        }
        planned = provided.plans[w] if provided is not None else planner(request)
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
        _week_of_box(
            rng,
            h,
            catalog,
            planned,
            cooked,
            w,
            ordered,
            factor,
            pantry,
            rows,
            price_sensitivity=price_sensitivity,
            feedback_rate=feedback_rate,
            drafted=provided.drafts[w] if provided is not None else None,
        )
    return rows


def _week_of_box(
    rng: random.Random,
    h: SimHousehold,
    catalog: Catalog,
    planned: Planned,
    cooked: list[str],
    w: int,
    ordered: bool,
    factor: dict[str, float],
    pantry: pantry_sim.Pantry,
    rows: Rows,
    *,
    price_sensitivity: float,
    feedback_rate: float,
    drafted: list[pantry_sim.Line] | None = None,
) -> None:
    """The box as drafted and as approved, its delivery into the pantry, the week's draw-down."""
    planned_dishes = [d for d in planned.meals.values() if d]
    if drafted is None:
        drafted = pantry_sim.draft_box(h, catalog, planned_dishes, factor, pantry)
    final, edits = pantry_sim.edit_box(
        rng,
        h,
        catalog,
        drafted,
        planned=planned_dishes,
        cooked=cooked,
        pantry=pantry,
        factor=factor,
        price_sensitivity=price_sensitivity,
    )
    delivered: dict[str, float] = defaultdict(float)
    if ordered:
        pantry_sim.deliver(pantry, final)
        for line in final:
            delivered[line.product] += line.base_units
    rows.boxes.append(
        {
            "household": h.key,
            "week": w,
            "ordered": ordered,
            "drafted_lines": len(drafted),
            "drafted_paise": pantry_sim.total_paise(drafted),
            "lines": len(final),
            "total_paise": pantry_sim.total_paise(final),
            "edits": len(edits),
            "spiked": sorted(
                line.product for line in drafted if factor[line.product] >= pantry_sim.SPIKE
            ),
        }
    )
    for line in final:
        rows.box_lines.append(
            {
                "household": h.key,
                "week": w,
                "product": line.product,
                "qty": line.qty,
                "step": line.step,
                "unit_price_paise": line.unit_price_paise,
                "reason": line.reason,
                "source": line.source,
                "replaces": line.replaces,
            }
        )
    for edit in edits:
        rows.edits.append({"household": h.key, "week": w, **edit})
    used: dict[str, float] = defaultdict(float)
    for c in rows.consumption:
        if c["household"] == h.key and c["week"] == w:
            used[c["product"]] = c["qty"]
    for row in pantry_sim.consume(
        rng, pantry, catalog, used, delivered, feedback_rate=feedback_rate
    ):
        rows.pantry.append({"household": h.key, "week": w, **row})


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
