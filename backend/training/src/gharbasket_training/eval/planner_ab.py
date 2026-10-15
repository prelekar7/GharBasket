"""RQ2: the CP-SAT meal planner against the rules fallback, a greedy and a random valid plan,
over simulator seeds (eval protocol §4, M-C).

For every seed the simulated households live the same weeks under each planner; the household
behaviour (accept, edit, skip) and the box are the simulator's, so the numbers compare planners,
not households. Metrics per seed, then mean ± 95 % CI across seeds; each arm (cpsat, rules)
against each baseline (greedy, random) as the per-seed paired difference, mean ± 95 % t-CI.
Planner v2 (`cpsat_v2`, the liking term) is paired with v1 (`cpsat`), over all weeks and over the
weeks from `FROM_WEEK` on: before a household has MIN_OBSERVED weeks of history v2 has no liking
to use and plans exactly as v1, so the earlier weeks only dilute the difference.
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from gharbasket_ml import planner as cpsat
from gharbasket_ml.schemas import PlanRequest, Weights
from gharbasket_sim import config
from gharbasket_sim.catalog import Catalog
from gharbasket_sim.households import SimHousehold, sample
from gharbasket_sim.weeks import MIN_OBSERVED, PLANNERS, Rows, cached, fasting_days, simulate

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


FROM_WEEK = MIN_OBSERVED  # the first week (0-based) in which v2 can differ from v1
MIN_SEEDS = 5  # the protocol's seeds; fewer give no verdict


@dataclass(frozen=True)
class PlannerRun:
    planner: str
    seed: int
    households: int
    weeks: int
    metrics: dict[str, float]
    # The same metrics over the weeks from FROM_WEEK on; empty when the run is shorter.
    later: dict[str, float] = field(default_factory=dict)


def _week_scores(
    h: SimHousehold, catalog: Catalog, rows: Rows, monday: date, from_week: int = 0
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
        if week_row["household"] != h.key or week_row["week"] < from_week:
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
        # Every hard rule, checked independently of the planner, under the relaxation it recorded.
        relax = frozenset(week_row["relaxed"])
        violations = cpsat.check(week, plan, relax)  # pyright: ignore[reportArgumentType]
        out["hard_rule_violations"].append(float(len(violations)))
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
    spans = {"all": 0, "later": FROM_WEEK}
    totals: dict[str, dict[str, list[float]]] = {span: {k: [] for k in METRICS} for span in spans}
    actions: dict[str, Counter[str]] = {span: Counter() for span in spans}
    boxes = dict.fromkeys(spans, 0)
    edits = dict.fromkeys(spans, 0)
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
        for span, first in spans.items():
            actions[span].update(m["action"] for m in rows.meals if m["week"] >= first)
            boxes[span] += sum(b["week"] >= first for b in rows.boxes)
            edits[span] += sum(e["week"] >= first for e in rows.edits)
            for key, values in _week_scores(h, catalog, rows, monday, first).items():
                totals[span][key].extend(values)

    def metrics(span: str) -> dict[str, float]:
        out = {k: (sum(v) / len(v) if v else 0.0) for k, v in totals[span].items()}
        planned = actions[span]["accept"] + actions[span]["edit"]
        out["acceptance"] = actions[span]["accept"] / planned if planned else 0.0
        out["edits_per_box"] = edits[span] / boxes[span] if boxes[span] else 0.0
        return out

    later = metrics("later") if weeks > FROM_WEEK else {}
    return PlannerRun(planner, seed, len(households), weeks, metrics("all"), later)


def summarise(runs: list[PlannerRun]) -> dict[str, dict[str, Estimate]]:
    """planner → metric → mean ± CI across seeds."""
    out: dict[str, dict[str, Estimate]] = {}
    for planner in sorted({r.planner for r in runs}):
        mine = [r for r in runs if r.planner == planner]
        out[planner] = {m: estimate([r.metrics[m] for r in mine]) for m in METRICS}
    return out


ARMS = ("cpsat", "rules", "cpsat_v2")  # the planners under test
BASELINES = ("greedy", "random")  # the protocol's baselines for M-C
# An arm paired with other baselines: v2 against v1, the same model without the liking term.
BASELINES_OF = {"cpsat_v2": ("cpsat",)}
V2, V1 = "cpsat_v2", "cpsat"
PRIMARY = "acceptance"
# Paired differences reported for every arm against every baseline, the primary metric first.
PAIRED = (PRIMARY, *(m for m in METRICS if m != PRIMARY), "perishables_fewer", "score_gain")


def objective(metrics: dict[str, float]) -> float:
    """The planner's objective on a run's means: 3 x preference + 5 x variety - 4 x perishables."""
    w = Weights()  # the planner's default weights (a request may override them)
    return (
        w.preference * metrics["preference"]
        + w.variety * metrics["variety"]
        - w.perishables * metrics["distinct_perishables"]
    )


def _difference(metric: str, arm: dict[str, float], base: dict[str, float]) -> float | None:
    if metric == "perishables_fewer":  # the share fewer distinct perishables than the baseline
        b = base["distinct_perishables"]
        return 1 - arm["distinct_perishables"] / b if b else None
    if metric == "score_gain":  # the objective's gain over the baseline, as a share
        b = objective(base)
        return (objective(arm) - b) / abs(b) if b else None
    return arm[metric] - base[metric]


def paired(
    runs: list[PlannerRun], *, later: bool = False, arms: tuple[str, ...] = ARMS
) -> dict[str, dict[str, dict[str, Estimate]]]:
    """arm → baseline → metric → mean ± 95 % CI of the per-seed difference (arm minus baseline;
    for the two shares, arm against baseline). Both planners face the same households and weeks in
    a seed, so the seed is the pair. With `later`, over the weeks from FROM_WEEK on only."""
    by = {(r.planner, r.seed): (r.later if later else r.metrics) for r in runs}
    out: dict[str, dict[str, dict[str, Estimate]]] = {}
    for arm in arms:
        for base in BASELINES_OF.get(arm, BASELINES):
            seeds = sorted(
                s for p, s in by if p == arm and by.get((base, s)) and by[arm, s] and by[base, s]
            )
            if not seeds:
                continue
            out.setdefault(arm, {})[base] = {
                metric: estimate(
                    [
                        d
                        for s in seeds
                        if (d := _difference(metric, by[arm, s], by[base, s])) is not None
                    ]
                )
                for metric in PAIRED
            }
    return out


def _check(e: Estimate, holds: bool | None, rule: str) -> dict[str, Any]:
    return {"value": e.mean, "ci95": e.ci, "n": e.n, "holds": holds, "rule": rule}


def _above_zero(e: Estimate) -> bool:
    """The paired 95 % interval lies above zero."""
    return e.n > 0 and e.mean - e.ci > 0


def against_target(
    summary: dict[str, dict[str, Estimate]],
    diffs: dict[str, dict[str, dict[str, Estimate]]],
    later: dict[str, dict[str, dict[str, Estimate]]] | None = None,
) -> dict[str, Any]:
    """The protocol's M-C hypotheses for the CP-SAT planner, the primary metric first, each with
    its estimate, interval and whether it holds; `verdict` is M-C on its primary metric
    (acceptance ≥ 70 % and above both baselines, with every hard rule kept)."""
    cp = summary.get("cpsat")
    if cp is None:
        return {}
    vs = diffs.get("cpsat", {})
    checks: dict[str, Any] = {
        "acceptance_ge_70pct": _check(
            cp[PRIMARY], cp[PRIMARY].mean >= 0.70, "mean acceptance ≥ 0.70"
        )
    }
    for base in BASELINES:
        if base in vs:
            e = vs[base][PRIMARY]
            checks[f"acceptance_over_{base}"] = _check(
                e, _above_zero(e), f"paired difference vs {base}: 95 % CI above 0"
            )
    v = cp["hard_rule_violations"]
    checks["hard_rules_100pct"] = _check(v, v.n > 0 and v.mean == 0, "no violation in any seed")
    if "greedy" in vs:
        fewer = vs["greedy"]["perishables_fewer"]
        checks["perishables_25pct_fewer_than_greedy"] = _check(
            fewer, fewer.n > 0 and fewer.mean >= 0.25, "mean per-seed share fewer ≥ 0.25"
        )
        gain = vs["greedy"]["score_gain"]
        checks["score_30pct_over_greedy"] = _check(
            gain, gain.n > 0 and gain.mean >= 0.30, "mean per-seed objective gain ≥ 0.30"
        )
    primary = [checks[k] for k in checks if k.startswith("acceptance_")]
    checks["verdict"] = {
        "hypothesis": "M-C on its primary metric",
        "holds": len(primary) == 1 + len(BASELINES)
        and all(c["holds"] for c in primary)
        and checks["hard_rules_100pct"]["holds"],
        "rule": "acceptance ≥ 0.70 and above greedy and random (paired CI), hard rules kept",
    }
    return checks | v2_checks(summary, diffs, later or {})


def v2_checks(
    summary: dict[str, dict[str, Estimate]],
    diffs: dict[str, dict[str, dict[str, Estimate]]],
    later: dict[str, dict[str, dict[str, Estimate]]],
) -> dict[str, Any]:
    """Planner v2 against v1 (handbook 08: acceptance ≥ v1's on the same seeds, hard rules kept,
    perishables no worse than v1 + 1), on the paired per-seed differences over the weeks from
    FROM_WEEK on (all weeks reported beside them). No verdict below MIN_SEEDS paired seeds."""
    if V2 not in summary or V1 not in summary:
        return {}
    weeks = f"weeks ≥ {FROM_WEEK}"
    vs = later.get(V2, {}).get(V1)
    if vs is None:  # a run too short to reach FROM_WEEK
        return {
            "v2_verdict": {
                "hypothesis": "planner v2 (liking) against v1",
                "holds": None,
                "rule": f"no {weeks} in the run",
            }
        }
    lift, perishables = vs[PRIMARY], vs["distinct_perishables"]
    n = lift.n
    checks: dict[str, Any] = {}
    if V1 in diffs.get(V2, {}):
        e = diffs[V2][V1][PRIMARY]
        checks["v2_acceptance_over_v1_all_weeks"] = _check(
            e, None, f"reference: weeks < {FROM_WEEK} are identical by construction"
        )
    lower_ok = n > 0 and lift.mean - lift.ci >= 0
    checks["v2_acceptance_over_v1"] = _check(
        lift,
        None if n < MIN_SEEDS else lower_ok,
        f"paired difference vs cpsat, {weeks}: 95 % CI lower bound ≥ 0",
    )
    v = summary[V2]["hard_rule_violations"]
    rules_ok = v.n > 0 and v.mean == 0
    checks["v2_hard_rules_100pct"] = _check(v, rules_ok, "no violation in any seed")
    within = perishables.n > 0 and perishables.mean <= 1
    checks["v2_perishables_within_1_of_v1"] = _check(
        perishables, within, f"mean paired difference in distinct perishables, {weeks}: ≤ +1"
    )
    checks["v2_verdict"] = {
        "hypothesis": "planner v2 (liking) against v1",
        "holds": None if n < MIN_SEEDS else lower_ok and rules_ok and within,
        "rule": f"acceptance not below v1 (paired CI lower bound ≥ 0, {weeks}), hard rules "
        f"kept, perishables ≤ v1 + 1; no verdict below {MIN_SEEDS} paired seeds",
    }
    return checks
