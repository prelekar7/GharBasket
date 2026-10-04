"""Spike S-4: can CP-SAT plan a week of meals from ~20 recipes with Jain/fasting rules in 5 s?

Run from the repository root (uses the API package for the seed households, OR-Tools on the side):

    uv run --with ortools==9.15.6755 python spikes/s4-meal-plan/plan.py

Model (handbook 08, M-C), for one household and one week:
- x[d, s, r] = 1 if recipe r is cooked for slot s (breakfast, lunch, dinner) on day d;
  t[d, s] = 1 if that lunch or dinner is a tiffin (only on the household's no-cook days).
- Hard rules: one choice per slot; diet (no egg dishes for veg households); Jain households only
  jain_ok dishes; on a fasting day at least one fasting_ok dish and, if the fast or festival says
  so, no onion or garlic all day; diabetic members: lunch and dinner diabetic_ok; cook time per
  meal within the weekday or weekend limit; no-cook days: tiffin for lunch and dinner and a
  no-cook breakfast; no recipe twice within k days (k=2 for breakfast, 3 for lunch and dinner);
  the week's food cost within the Budget Lock.
- Soft goals (weighted sum): cuisine preference, variety (distinct dishes), ingredient reuse
  (fewer distinct perishables to buy, so less waste).

Every plan is re-checked by an independent checker, and compared with random and greedy plans.
"""

import json
import random
import statistics
import sys
import time
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from pathlib import Path

from ortools.sat.python import cp_model

ROOT = Path(__file__).resolve().parents[2]
SEED = ROOT / "data" / "seeds" / "catalog"
sys.path.insert(0, str(ROOT / "backend" / "api" / "src"))
from gharbasket.tools.seed import HOUSEHOLDS  # noqa: E402

SLOTS = ("breakfast", "lunch", "dinner")
WINDOW = {"breakfast": 2, "lunch": 3, "dinner": 3}  # no repeat within this many days
PERISHABLE_H = 96  # shelf life at or below this counts as perishable
WEEKS_PER_MONTH = 4.33
TIME_LIMIT_S = 5.0
WEIGHTS = {"preference": 3, "variety": 5, "perishables": 4}
BASE = {"g": 1, "kg": 1000, "ml": 1, "l": 1000, "piece": 1, "bunch": 1}


# --- data -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Recipe:
    slug: str
    cuisine: str
    slots: frozenset[str]
    minutes: int
    diet: str
    jain_ok: bool
    no_onion_garlic: bool
    fasting_ok: bool
    diabetic_ok: bool
    no_cook: bool
    cost_per_serving: int  # paise at the seed hub
    perishables: frozenset[str]


@dataclass(frozen=True)
class Household:
    name: str
    size: int
    diet: str
    jain: bool
    cuisines: frozenset[str]
    weekday_minutes: int
    weekend_minutes: int
    no_cook_days: frozenset[int]
    weekly_budget: int | None  # paise
    diabetic: bool
    fasting_days: dict[int, bool] = field(default_factory=dict)  # day → no onion/garlic too


def load_recipes() -> list[Recipe]:
    products = {p["slug"]: p for p in json.loads((SEED / "products.json").read_text())["products"]}
    prices = {i["slug"]: i["price_paise"] for i in json.loads((SEED / "prices.json").read_text())["items"]}
    recipes = []
    for r in json.loads((SEED / "recipes.json").read_text())["recipes"]:
        if r["tags"] == ["roti"]:
            continue  # a side (chapati), not a meal: the planner adds sides later
        tags: set[str] = set()
        cost = 0.0
        perishables: set[str] = set()
        for i in r["ingredients"]:
            product = products[i["product"]]
            tags |= set(product["tags"])
            step = product["step"]["qty"] * BASE[product["step"]["unit"]]
            cost += i["qty"] / step * prices[i["product"]]
            if (product["shelf_life_h"] or 0) <= PERISHABLE_H:
                perishables.add(i["product"])
        no_onion_garlic = "onion_garlic" not in tags
        diet = "egg" if "egg" in tags else "veg"
        recipes.append(
            Recipe(
                slug=r["slug"],
                cuisine=r["cuisine"],
                slots=frozenset(r["slots"]),
                minutes=r["cook_minutes"],
                diet=diet,
                jain_ok=no_onion_garlic and "root" not in tags and diet == "veg",
                no_onion_garlic=no_onion_garlic,
                fasting_ok=r["fasting_ok"],
                diabetic_ok=r["diabetic_ok"],
                no_cook="no_cook" in r["tags"],
                cost_per_serving=round(cost),
                perishables=frozenset(perishables),
            )
        )
    return recipes


def festival_days(week_start: date) -> dict[str, list[int]]:
    """code → weekday indexes (0 = Monday) of the week that are that festival."""
    found: dict[str, list[int]] = {}
    for f in json.loads((SEED / "festivals.json").read_text())["festivals"]:
        day = date.fromisoformat(f["date"])
        if week_start <= day < week_start + timedelta(days=7):
            found.setdefault(f["code"], []).append((day - week_start).days)
    return found


def load_households(week_start: date) -> list[Household]:
    names = {"+919800001001": "Priya", "+919800001002": "Sunita", "+919800001003": "Ramesh", "+919800001004": "Meena"}
    festivals = festival_days(week_start)
    weekly = {"tuesdays": [1]}
    out = []
    for phone, h in HOUSEHOLDS.items():
        members = h["members"]
        fasting: dict[int, bool] = {}
        for m in members:
            rules = m.get("fasting_rules", {})
            for fast in rules.get("fasts", []):
                for day in festivals.get(fast, []) + weekly.get(fast, []):
                    fasting[day] = fasting.get(day, False) or bool(rules.get("no_onion_garlic_on_fast_days"))
        budget = h["budget_paise"]
        out.append(
            Household(
                name=names[phone],
                size=len(members),
                diet=h["diet"],
                jain=any(m.get("fasting_rules", {}).get("jain") for m in members),
                cuisines=frozenset(h["cuisine_tags"]),
                weekday_minutes=h["weekday_cook_minutes"],
                weekend_minutes=h["weekend_cook_minutes"],
                no_cook_days=frozenset(h["no_cook_days"]),
                weekly_budget=round(budget / WEEKS_PER_MONTH) if budget else None,
                diabetic=any("diabetes" in m.get("health_tags", []) for m in members),
                fasting_days=fasting,
            )
        )
    return out


# --- rules shared by the model and the checker ---------------------------------------------------


RULES = ("diet", "jain", "diabetic", "fast_no_onion_garlic", "cook_time", "fasting_dish", "repeat", "budget")


def allowed(h: Household, r: Recipe, day: int, slot: str, relax: frozenset[str] = frozenset()) -> bool:
    """Hard rules that depend only on one (day, slot, recipe). `relax` switches rules off."""
    if slot not in r.slots:
        return False
    if "diet" not in relax and h.diet == "veg" and r.diet == "egg":
        return False
    if "jain" not in relax and h.jain and not r.jain_ok:
        return False
    if "diabetic" not in relax and h.diabetic and slot != "breakfast" and not r.diabetic_ok:
        return False
    if "fast_no_onion_garlic" not in relax and h.fasting_days.get(day) and not r.no_onion_garlic:
        return False
    limit = h.weekend_minutes if day >= 5 else h.weekday_minutes
    if "cook_time" not in relax and r.minutes > limit:
        return False
    if day in h.no_cook_days:
        return slot == "breakfast" and r.no_cook
    return True


def cost(h: Household, r: Recipe) -> int:
    return r.cost_per_serving * h.size


Plan = dict[tuple[int, str], str | None]  # (day, slot) → recipe slug, or None for a tiffin


def check(h: Household, recipes: dict[str, Recipe], plan: Plan) -> list[str]:
    """Every hard rule, re-checked independently of the model. Empty list = valid."""
    problems = []
    for day in range(7):
        for slot in SLOTS:
            slug = plan.get((day, slot), "missing")
            if slug == "missing":
                problems.append(f"day {day} {slot}: nothing planned")
            elif slug is None:
                if not (day in h.no_cook_days and slot != "breakfast"):
                    problems.append(f"day {day} {slot}: tiffin on a cooking day")
            elif not allowed(h, recipes[slug], day, slot):
                problems.append(f"day {day} {slot}: {slug} breaks a rule")
        if day in h.fasting_days:
            today = [plan[(day, s)] for s in SLOTS]
            if not any(s and recipes[s].fasting_ok for s in today):
                problems.append(f"day {day}: fasting day without a fasting dish")
    for slot in SLOTS:
        for start in range(7):
            window = [plan[(d, slot)] for d in range(start, min(7, start + WINDOW[slot]))]
            if slot != "breakfast":
                other = "dinner" if slot == "lunch" else "lunch"
                window += [plan[(d, other)] for d in range(start, min(7, start + WINDOW[slot]))]
            used = [s for s in window if s]
            if len(used) != len(set(used)):
                problems.append(f"{slot} from day {start}: a dish repeats within {WINDOW[slot]} days")
    if h.weekly_budget is not None:
        spent = sum(cost(h, recipes[s]) for s in plan.values() if s)
        if spent > h.weekly_budget:
            problems.append(f"over budget: {spent} > {h.weekly_budget}")
    return problems


def score(h: Household, recipes: dict[str, Recipe], plan: Plan) -> dict[str, int]:
    used = [recipes[s] for s in plan.values() if s]
    return {
        "preference": sum(r.cuisine in h.cuisines for r in used),
        "variety": len({r.slug for r in used}),
        "perishables": len(set().union(*(r.perishables for r in used))) if used else 0,
        "cost_paise": sum(cost(h, r) for r in used),
    }


def objective(s: dict[str, int]) -> int:
    return (
        WEIGHTS["preference"] * s["preference"]
        + WEIGHTS["variety"] * s["variety"]
        - WEIGHTS["perishables"] * s["perishables"]
    )


# --- the CP-SAT model --------------------------------------------------------------------------


@dataclass
class Result:
    status: str
    seconds: float
    plan: Plan | None
    objective: float | None = None


def solve(
    h: Household,
    recipes: list[Recipe],
    time_limit: float = TIME_LIMIT_S,
    workers: int = 8,
    relax: frozenset[str] = frozenset(),
) -> Result:
    m = cp_model.CpModel()
    x: dict[tuple[int, str, str], cp_model.IntVar] = {}
    tiffin: dict[tuple[int, str], cp_model.IntVar] = {}
    for d in range(7):
        for s in SLOTS:
            for r in recipes:
                if allowed(h, r, d, s, relax):
                    x[d, s, r.slug] = m.new_bool_var(f"x_{d}_{s}_{r.slug}")
            choices = [x[d, s, r.slug] for r in recipes if (d, s, r.slug) in x]
            if d in h.no_cook_days and s != "breakfast":
                tiffin[d, s] = m.new_bool_var(f"t_{d}_{s}")
                choices.append(tiffin[d, s])
            m.add_exactly_one(choices)

    by_recipe = {r.slug: r for r in recipes}
    # No repeats within k days (lunch and dinner share their window, so no dish twice a day).
    # Relaxed, the repeat rule still keeps a dish from appearing twice on the same day.
    windows = (1, 1) if "repeat" in relax else (WINDOW["breakfast"], WINDOW["lunch"])
    for r in recipes:
        for slot_group, k in ((("breakfast",), windows[0]), (("lunch", "dinner"), windows[1])):
            for start in range(7):
                window = [x[d, s, r.slug] for d in range(start, min(7, start + k)) for s in slot_group if (d, s, r.slug) in x]
                if len(window) > 1:
                    m.add(sum(window) <= 1)
    # A fasting day has at least one fasting dish.
    for d in h.fasting_days if "fasting_dish" not in relax else {}:
        fasting = [v for (dd, _, slug), v in x.items() if dd == d and by_recipe[slug].fasting_ok]
        m.add(sum(fasting) >= 1)
    # The week's food cost fits the Budget Lock.
    spend = sum(cost(h, by_recipe[slug]) * v for (_, _, slug), v in x.items())
    if h.weekly_budget is not None and "budget" not in relax:
        m.add(spend <= h.weekly_budget)

    # Soft goals.
    preference = sum(v for (_, _, slug), v in x.items() if by_recipe[slug].cuisine in h.cuisines)
    used = {}
    for r in recipes:
        uses = [v for (_, _, slug), v in x.items() if slug == r.slug]
        if uses:
            used[r.slug] = m.new_bool_var(f"used_{r.slug}")
            m.add_max_equality(used[r.slug], uses)
    bought = {}
    for p in set().union(*(r.perishables for r in recipes)):
        users = [used[r.slug] for r in recipes if p in r.perishables and r.slug in used]
        if users:
            bought[p] = m.new_bool_var(f"buy_{p}")
            m.add_max_equality(bought[p], users)
    m.maximize(
        WEIGHTS["preference"] * preference
        + WEIGHTS["variety"] * sum(used.values())
        - WEIGHTS["perishables"] * sum(bought.values())
    )

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_workers = workers
    solver.parameters.random_seed = 7
    started = time.perf_counter()
    status = solver.solve(m)
    seconds = time.perf_counter() - started
    name = solver.status_name(status)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return Result(name, seconds, None)
    objective_value = solver.objective_value
    plan: Plan = {}
    for d in range(7):
        for s in SLOTS:
            plan[d, s] = next((slug for (dd, ss, slug), v in x.items() if dd == d and ss == s and solver.value(v)), None)
    return Result(name, seconds, plan, objective_value)


def diagnose(h: Household, recipes: list[Recipe]) -> list[str]:
    """Which single rule, switched off, makes an infeasible household plannable?"""
    return [rule for rule in RULES if solve(h, recipes, time_limit=2, relax=frozenset({rule})).plan]


# Comfort rules may be relaxed, in this order, when a household cannot be planned; safety and
# religious rules (diet, Jain, diabetic, onion/garlic on fasts, a fasting dish) never are.
# Relaxing "repeat" allows a dish again on a later day, never twice on the same day.
FALLBACK = ("repeat", "cook_time", "budget")


def plan_with_fallback(h: Household, recipes: list[Recipe]) -> tuple[Result, tuple[str, ...]]:
    """The strict plan, else the first relaxation (one rule, then two) that gives one."""
    strict = solve(h, recipes)
    if strict.plan:
        return strict, ()
    for n in (1, 2):
        for start in range(len(FALLBACK) - n + 1):
            relaxed = FALLBACK[start : start + n]
            result = solve(h, recipes, relax=frozenset(relaxed))
            if result.plan:
                return result, relaxed
    return strict, ()


def candidates(h: Household, recipes: list[Recipe]) -> dict[str, int]:
    """How many recipes are allowed per slot on a weekday (the size of the menu each rule leaves)."""
    return {s: sum(allowed(h, r, 0, s) for r in recipes) for s in SLOTS}


# --- baselines ----------------------------------------------------------------------------------


def random_valid(h: Household, recipes: list[Recipe], rng: random.Random, tries: int = 20000) -> Plan | None:
    by_slug = {r.slug: r for r in recipes}
    for _ in range(tries):
        plan: Plan = {}
        for d in range(7):
            for s in SLOTS:
                options = [r.slug for r in recipes if allowed(h, r, d, s)]
                if d in h.no_cook_days and s != "breakfast":
                    options = [None]  # type: ignore[list-item]
                if not options:
                    break
                plan[d, s] = rng.choice(options)
        if len(plan) == 21 and not check(h, by_slug, plan):
            return plan
    return None


def greedy(h: Household, recipes: list[Recipe]) -> Plan | None:
    """Day by day, the allowed dish with the best preference that keeps the rules so far."""
    by_slug = {r.slug: r for r in recipes}
    plan: Plan = {}
    for d in range(7):
        for s in SLOTS:
            if d in h.no_cook_days and s != "breakfast":
                plan[d, s] = None
                continue
            ranked = sorted(
                (r for r in recipes if allowed(h, r, d, s)),
                key=lambda r: (-(r.cuisine in h.cuisines), r.cost_per_serving, r.slug),
            )
            for r in ranked:
                plan[d, s] = r.slug
                partial = {k: v for k, v in plan.items()}
                if not _partial_problems(h, by_slug, partial, d):
                    break
            else:
                return None
    return plan if not check(h, by_slug, plan) else None


def _partial_problems(h: Household, recipes: dict[str, Recipe], plan: Plan, upto: int) -> bool:
    """Repeat-window and budget problems among the days planned so far (fasting checked at the end)."""
    for slot_group, k in ((("breakfast",), 2), (("lunch", "dinner"), 3)):
        for start in range(upto + 1):
            window = [plan.get((d, s)) for d in range(start, min(upto + 1, start + k)) for s in slot_group]
            used = [s for s in window if s]
            if len(used) != len(set(used)):
                return True
    if h.weekly_budget is not None:
        if sum(cost(h, recipes[s]) for s in plan.values() if s) > h.weekly_budget:
            return True
    return False


# --- the experiment -------------------------------------------------------------------------------


def show(h: Household, plan: Plan) -> None:
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    for d in range(7):
        cells = [(plan[d, s] or "(tiffin)") for s in SLOTS]
        flag = " fasting" if d in h.fasting_days else ""
        print(f"    {days[d]}{flag:<8} " + " | ".join(f"{c:<18}" for c in cells))


def scaled(recipes: list[Recipe], factor: int, rng: random.Random) -> list[Recipe]:
    """A bigger catalog for the timing test: copies with jittered cost and cook time."""
    out = list(recipes)
    for n in range(1, factor):
        for r in recipes:
            out.append(replace(
                r,
                slug=f"{r.slug}-v{n}",
                cost_per_serving=max(1, round(r.cost_per_serving * rng.uniform(0.8, 1.2))),
                minutes=max(5, r.minutes + rng.choice([-5, 0, 5])),
            ))
    return out


def main() -> None:
    week = date(2026, 10, 12)  # Monday; Navratri (unverified seed dates) covers the whole week
    recipes = load_recipes()
    by_slug = {r.slug: r for r in recipes}
    households = load_households(week)
    variants = [replace(households[0], name="Priya + Tuesday fast", fasting_days={1: True})]
    print(f"{len(recipes)} recipes (chapati left out as a side); week of {week}\n")

    rows = []
    for h in households + variants:
        result = solve(h, recipes)
        print(f"{h.name}: size {h.size}, diet {h.diet}, jain {h.jain}, diabetic {h.diabetic}, "
              f"fasting days {sorted(h.fasting_days)}, no-cook {sorted(h.no_cook_days)}, "
              f"budget/week {h.weekly_budget}")
        print(f"  CP-SAT {result.status} in {result.seconds:.3f} s")
        if result.plan is None:
            print(f"  allowed on a weekday per slot: {candidates(h, recipes)}")
            print(f"  plannable if one rule is switched off: {diagnose(h, recipes) or 'none alone'}")
            fallback, relaxed = plan_with_fallback(h, recipes)
            if fallback.plan:
                print(f"  fallback: {fallback.status} in {fallback.seconds:.3f} s with {relaxed} relaxed; "
                      f"safety and religious rules still hold: "
                      f"{not [p for p in check(h, by_slug, fallback.plan) if 'repeat' not in p and 'over budget' not in p]}")
                show(h, fallback.plan)
            print()
            rows.append((h.name, result.status, result.seconds, None, None, None))
            continue
        problems = check(h, by_slug, result.plan)
        print(f"  checker: {'all rules hold' if not problems else problems}")
        show(h, result.plan)
        s_cp = score(h, by_slug, result.plan)
        rng = random.Random(1)
        rand = [p for p in (random_valid(h, recipes, rng) for _ in range(20)) if p]
        g = greedy(h, recipes)
        s_rand = [score(h, by_slug, p) for p in rand]
        s_g = score(h, by_slug, g) if g else None
        print(f"  CP-SAT  {s_cp}  objective {objective(s_cp)}")
        if s_rand:
            print("  random  mean objective "
                  f"{statistics.mean(objective(s) for s in s_rand):.1f} over {len(s_rand)} valid plans, "
                  f"mean perishables {statistics.mean(s['perishables'] for s in s_rand):.1f}")
        print(f"  greedy  {s_g}  objective {objective(s_g) if s_g else 'no valid plan'}\n")
        rows.append((h.name, result.status, result.seconds, s_cp, s_rand, s_g))

    print("Timing: Priya, 5 runs each, 8 workers, 5 s limit")
    rng = random.Random(3)
    for factor in (1, 3, 6):
        catalog = scaled(recipes, factor, rng)
        times, statuses = [], []
        for _ in range(5):
            r = solve(households[0], catalog)
            times.append(r.seconds)
            statuses.append(r.status)
        print(f"  {len(catalog):>3} recipes: median {statistics.median(times):.3f} s, "
              f"max {max(times):.3f} s, {', '.join(sorted(set(statuses)))}")

    best = solve(households[0], recipes).objective
    print(f"\nOne worker (a small server CPU), Priya, {len(recipes)} recipes; 8-worker optimum {best:.0f}")
    for limit in (0.25, 0.5, 1.0, 2.0, 5.0):
        r = solve(households[0], recipes, time_limit=limit, workers=1)
        print(f"  limit {limit:>4} s: {r.status:<8} objective {r.objective:.0f} "
              f"({100 * (r.objective or 0) / best:.0f}% of the optimum)")


if __name__ == "__main__":
    main()
