"""RQ2: the CP-SAT meal planner against the rules fallback, a greedy and a random valid plan,
over simulator seeds (eval protocol §4, M-C).

For every seed the simulated households live the same weeks under each planner; the household
behaviour (accept, edit, skip) and the box are the simulator's, so the numbers compare planners,
not households. Metrics per seed, then mean ± 95 % CI across seeds.
"""

from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Any

from gharbasket_ml import planner as cpsat
from gharbasket_ml.schemas import PlanRequest
from gharbasket_sim import config
from gharbasket_sim.catalog import Catalog
from gharbasket_sim.households import SimHousehold, sample
from gharbasket_sim.weeks import PLANNERS, Rows, cached, fasting_days, simulate

from gharbasket_training.eval.stats import Estimate, estimate

METRICS = (
    "acceptance",  # share of planned dishes the household accepted as planned
    "edits_per_box",  # line edits a week
    "distinct_perishables",  # perishable products a week's plan needs
    "variety",  # distinct dishes a week
    "preference",  # dishes of the household's own cuisines a week
    "relaxed_share",  # weeks that needed a comfort rule relaxed
    "infeasible_share",  # weeks with no plan at all
    "cost_paise",  # the plan's cost a week at list prices
    "hard_rule_violations",  # planned (day, slot, dish) that break a hard rule: must be 0
)


@dataclass(frozen=True)
class PlannerRun:
    planner: str
    seed: int
    households: int
    weeks: int
    metrics: dict[str, float]


def _week_scores(
    h: SimHousehold, catalog: Catalog, rows: Rows, monday: date
) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {
        k: [] for k in METRICS if k not in ("acceptance", "edits_per_box")
    }
    by_week: dict[int, dict[tuple[int, str], str | None]] = {}
    for meal in rows.meals:
        if meal["household"] != h.key:
            continue
        by_week.setdefault(meal["week"], {})[(meal["date"].weekday(), meal["slot"])] = meal[
            "planned"
        ]
    for week_row in rows.weeks:
        if week_row["household"] != h.key:
            continue
        w = week_row["week"]
        plan = by_week.get(w, {})
        planned = {k: v for k, v in plan.items() if v}
        out["relaxed_share"].append(float(bool(week_row["relaxed"])))
        out["infeasible_share"].append(float(not planned))
        if not planned:
            out["distinct_perishables"].append(0.0)
            out["variety"].append(0.0)
            out["preference"].append(0.0)
            out["cost_paise"].append(0.0)
            out["hard_rule_violations"].append(0.0)
            continue
        request = {
            "household": h.rules,
            "fasting_days": fasting_days(h, week_row["monday"], catalog.festivals),
            "recipes": catalog.recipes,
        }
        week = cpsat.Week.of(PlanRequest.model_validate(request))
        score = cpsat.score(week, plan)  # pyright: ignore[reportArgumentType]
        out["distinct_perishables"].append(float(score.perishables))
        out["variety"].append(float(score.variety))
        out["preference"].append(float(score.preference))
        out["cost_paise"].append(float(score.cost_paise))
        relax = frozenset(week_row["relaxed"])
        violations = sum(
            not cpsat.allowed(week, week.recipes[slug], day, slot, relax)  # pyright: ignore[reportArgumentType]
            for (day, slot), slug in planned.items()
            if slug in week.recipes
        )
        out["hard_rule_violations"].append(float(violations))
    return out


def run_planner(
    planner: str,
    seed: int,
    catalog: Catalog,
    *,
    weeks: int,
    start: date,
    adoption: float | None,
    shocks: config.Shocks | None,
) -> PlannerRun:
    cohorts = config.load()
    households = sample(seed, cohorts, catalog, adoption)
    by_name = {c.name: c for c in cohorts.cohorts}
    plan = cached(PLANNERS[planner])
    monday = start
    totals: dict[str, list[float]] = {k: [] for k in METRICS}
    actions: Counter[str] = Counter()
    boxes = edits = 0
    for h in households:
        c = by_name[h.cohort]
        rows = simulate(
            seed,
            h,
            catalog,
            monday,
            weeks,
            plan,
            shocks,
            price_sensitivity=c.price_sensitivity,
            feedback_rate=c.feedback_rate,
        )
        actions.update(m["action"] for m in rows.meals)
        boxes += len(rows.boxes)
        edits += len(rows.edits)
        for key, values in _week_scores(h, catalog, rows, monday).items():
            totals[key].extend(values)
    planned = actions["accept"] + actions["edit"]
    metrics = {k: (sum(v) / len(v) if v else 0.0) for k, v in totals.items()}
    metrics["acceptance"] = actions["accept"] / planned if planned else 0.0
    metrics["edits_per_box"] = edits / boxes if boxes else 0.0
    return PlannerRun(planner, seed, len(households), weeks, metrics)


def summarise(runs: list[PlannerRun]) -> dict[str, dict[str, Estimate]]:
    """planner → metric → mean ± CI across seeds."""
    out: dict[str, dict[str, Estimate]] = {}
    for planner in sorted({r.planner for r in runs}):
        mine = [r for r in runs if r.planner == planner]
        out[planner] = {m: estimate([r.metrics[m] for r in mine]) for m in METRICS}
    return out


def against_target(summary: dict[str, dict[str, Estimate]]) -> dict[str, Any]:
    """The protocol's hypotheses for M-C, each with the number and whether it holds."""
    cp = summary.get("cpsat")
    greedy = summary.get("greedy")
    checks: dict[str, Any] = {}
    if cp is not None:
        checks["hard_rules_100pct"] = {
            "value": cp["hard_rule_violations"].mean,
            "holds": cp["hard_rule_violations"].mean == 0,
        }
        checks["acceptance_ge_70pct"] = {
            "value": cp["acceptance"].mean,
            "holds": cp["acceptance"].mean >= 0.70,
        }
    if cp is not None and greedy is not None and greedy["distinct_perishables"].mean:
        fewer = 1 - cp["distinct_perishables"].mean / greedy["distinct_perishables"].mean
        checks["perishables_25pct_fewer_than_greedy"] = {"value": fewer, "holds": fewer >= 0.25}
        own = (
            cp["preference"].mean * 3 + cp["variety"].mean * 5 - cp["distinct_perishables"].mean * 4
        )
        base = (
            greedy["preference"].mean * 3
            + greedy["variety"].mean * 5
            - greedy["distinct_perishables"].mean * 4
        )
        better = (own - base) / abs(base) if base else 0.0
        checks["score_30pct_over_greedy"] = {"value": better, "holds": better >= 0.30}
    return checks
