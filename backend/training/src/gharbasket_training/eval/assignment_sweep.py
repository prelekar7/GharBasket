"""RQ4: what fairness costs in home-kitchen assignment (eval protocol §4, §5 and §7, M-G).

One simulated week per seed. The simulated households that take tiffins (those with a no-cook
day) order dinner portions at their society on each of the week's 7 evenings: every one of them
on its no-cook day (Sunday in the simulator), and on each other evening with probability
`WEEKDAY_TIFFIN_SHARE` (the simulator has no weekday tiffin demand yet, so the harness draws it).
`KITCHENS` home kitchens around the hub cook them. Every kitchen starts the week at 0 earnings and
each evening's assignment sees the earnings the week has given it so far, so λ balances the week.

Per seed, the greedy nearest kitchen and the CP-SAT model at every λ in `LAMBDAS` live the same
week; the week is scored by the Gini of the kitchens' weekly earnings and the cost a portion
(distance + quality penalty). A seed is the pair: the M-G target (Gini ≤ 0.25 at ≤ +5 % cost
against greedy) is judged on the per-seed cost_λ / cost_greedy - 1 and Gini_λ, mean ± 95 % t-CI.
"""

import hashlib
import importlib.metadata
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gharbasket_ml import assignment
from gharbasket_ml.schemas import AssignRequest, AssignResponse
from gharbasket_sim import config
from gharbasket_sim.catalog import Catalog
from gharbasket_sim.households import sample

from gharbasket.tools import seed as world
from gharbasket.tools.seed import SOCIETIES
from gharbasket_training.eval.stats import Estimate, estimate

# Eval protocol §5: paise of cost per portion of earnings imbalance (M10 §3.5).
LAMBDAS = (0.0, 100.0, 200.0, 300.0, 500.0, 700.0, 1000.0, 2000.0)
HUB = (19.0300, 73.0690)  # the Kharghar hub (spike S-3)
KITCHENS = 6
EVENINGS = 7  # Monday to Sunday
WEEKDAY_TIFFIN_SHARE = 0.5  # a tiffin household's chance of ordering on an evening it cooks
MAX_KM = 8.0
TIME_LIMIT_S = 2.0  # deterministic time per evening (`assign(..., deterministic=True)`)
COST_LIMIT = 0.05  # the target: at most +5 % cost a portion against greedy ...
GINI_LIMIT = 0.25  # ... at a Gini of weekly earnings of at most 0.25
MIN_SEEDS = 5  # the protocol's seeds; fewer give no verdict


@dataclass(frozen=True)
class Week:
    """A seed's week: each evening's tiffin orders (`TiffinOrderIn` fields) and the kitchens
    (`KitchenIn` fields without capacity or earnings, which change by the evening)."""

    evenings: list[list[dict[str, Any]]]
    kitchens: list[dict[str, Any]]

    @property
    def orders(self) -> int:
        return sum(len(e) for e in self.evenings)

    @property
    def portions(self) -> int:
        return sum(o["portions"] for e in self.evenings for o in e)


@dataclass(frozen=True)
class SweepPoint:
    fairness_lambda: float | None  # None = greedy nearest kitchen
    # "solved" when every evening's plan assigned every order and, for a λ, came from CP-SAT;
    # otherwise the first other evening status ("partial", "fallback", "infeasible").
    status: str
    evening_status: list[str]
    greedy_evenings: int  # evenings greedy answered (for a λ: CP-SAT fell back)
    seconds: float  # the week's solve time
    gini: float  # of the week's earnings, over the kitchens eligible on some evening
    gini_all: float  # of the week's earnings, over every kitchen
    cost_paise_per_portion: float  # distance + quality penalty, over the week's portions
    rider_paise_per_portion: float  # the distance part alone
    km_per_portion: float
    kitchens_used: int

    @property
    def solved(self) -> bool:
        return self.status == "solved"


@dataclass(frozen=True)
class SweepRun:
    seed: int
    evenings: int  # with at least one order
    orders: int
    portions: int
    points: list[SweepPoint]

    @property
    def solved(self) -> bool:
        """Every point solved: only then is the seed a pair for every λ."""
        return all(p.solved for p in self.points)

    def point(self, fairness_lambda: float | None) -> SweepPoint | None:
        return next((p for p in self.points if p.fairness_lambda == fairness_lambda), None)


def key(fairness_lambda: float | None) -> str:
    return "greedy" if fairness_lambda is None else f"{fairness_lambda:g}"


def settings() -> dict[str, Any]:
    """What the sweep's numbers came from, for the report's provenance."""
    fields = AssignRequest.model_fields
    seed_file = Path(world.__file__)
    return {
        "lambdas": list(LAMBDAS),
        "kitchens": KITCHENS,
        "hub": list(HUB),
        "evenings": EVENINGS,
        "weekday_tiffin_share": WEEKDAY_TIFFIN_SHARE,
        "kitchen_capacity": "max(8, the evening's portions // 3)",
        "max_km": MAX_KM,
        "time_limit_s": TIME_LIMIT_S,
        "deterministic": True,
        "paise_per_portion": fields["paise_per_portion"].default,
        "quality_paise_per_star": fields["quality_paise_per_star"].default,
        "rider_paise_per_portion_km": assignment.PAISE_PER_PORTION_KM,
        "rating_floor": fields["rating_floor"].default,
        "max_kitchens_per_order": fields["max_kitchens_per_order"].default,
        "model": assignment.MODEL,
        "baseline": assignment.GREEDY,
        "ortools_version": importlib.metadata.version("ortools"),
        "societies_source": "gharbasket.tools.seed.SOCIETIES",
        "societies_sha256": hashlib.sha256(seed_file.read_bytes()).hexdigest(),
        "gini_population": "kitchens eligible on at least one evening of the week",
    }


def _centroid(boundary: list[tuple[float, float]]) -> tuple[float, float]:
    lat = sum(p[0] for p in boundary) / len(boundary)
    lng = sum(p[1] for p in boundary) / len(boundary)
    return lat, lng


def instance(seed: int, catalog: Catalog, adoption: float | None) -> Week | None:
    """A week of tiffin orders from the simulated households, and the kitchens around the hub."""
    centres = {str(s.id): _centroid(s.boundary) for s in SOCIETIES if s.boundary}
    takers = [
        h
        for h in sample(seed, config.load(), catalog, adoption)
        if h.rules["no_cook_days"] and h.society_id in centres
    ]
    evenings: list[list[dict[str, Any]]] = []
    for day in range(EVENINGS):
        rng = random.Random(f"gharbasket-eval-assign:{seed}:{day}")  # noqa: S311 (seeded)
        tonight = []
        for h in takers:
            draw = rng.random()  # one draw per household and evening, ordered or not
            if day not in h.rules["no_cook_days"] and draw >= WEEKDAY_TIFFIN_SHARE:
                continue
            lat, lng = centres[h.society_id]
            tonight.append(
                {
                    "id": f"{h.key}-d{day}",
                    "lat": lat + rng.uniform(-0.0005, 0.0005),
                    "lng": lng + rng.uniform(-0.0005, 0.0005),
                    "portions": h.rules["size"],
                    "skills": ["veg", "jain"] if h.rules["jain"] else ["veg"],
                }
            )
        evenings.append(tonight)
    if not any(evenings):
        return None
    rng = random.Random(f"gharbasket-eval-assign:{seed}")  # noqa: S311 (seeded)
    kitchens = [
        {
            "id": f"kitchen-{k}",
            "lat": HUB[0] + rng.uniform(-0.03, 0.03),
            "lng": HUB[1] + rng.uniform(-0.04, 0.04),
            "skills": ["veg", "jain"] if k % 2 == 0 else ["veg"],
            "rating": round(rng.uniform(3.9, 4.9), 1),
        }
        for k in range(KITCHENS)
    ]
    return Week(evenings, kitchens)


def request(
    orders: list[dict[str, Any]],
    kitchens: list[dict[str, Any]],
    earnings: dict[str, int],
    fairness_lambda: float,
) -> AssignRequest:
    portions = sum(o["portions"] for o in orders)
    return AssignRequest.model_validate(
        {
            "orders": orders,
            "kitchens": [
                {
                    **k,
                    "max_portions": max(8, portions // 3),
                    "earnings_paise": earnings[k["id"]],
                }
                for k in kitchens
            ],
            "fairness_lambda": fairness_lambda,
            "max_km": MAX_KM,
            "time_limit_s": TIME_LIMIT_S,
        }
    )


def run_week(week: Week, fairness_lambda: float | None) -> SweepPoint:
    """The week's 7 evenings at one λ (None: greedy), each kitchen's earnings carried forward
    from 0."""
    earnings = {k["id"]: 0 for k in week.kitchens}
    eligible: set[str] = set()
    used: set[str] = set()
    statuses: list[str] = []
    greedy_evenings = 0
    seconds = 0.0
    portions = cost = rider = metres = 0
    for orders in week.evenings:
        if not orders:
            continue
        req = request(orders, week.kitchens, earnings, fairness_lambda or 0.0)
        pairs = assignment.candidates(req)
        answer: AssignResponse = (
            assignment.baseline(req)
            if fairness_lambda is None
            else assignment.assign(req, deterministic=True)
        )
        status = answer.status
        if fairness_lambda is None and status == "fallback":
            status = "solved"  # greedy is the baseline's own answer, every order assigned
        statuses.append(status)
        greedy_evenings += answer.model == assignment.GREEDY
        seconds += answer.seconds
        eligible |= {req.kitchens[c.kitchen].id for c in pairs}
        by_pair = {(req.orders[c.order].id, req.kitchens[c.kitchen].id): c for c in pairs}
        for a in answer.assignments:
            c = by_pair[a.order_id, a.kitchen_id]
            portions += a.portions
            cost += c.cost_paise * a.portions
            rider += c.metres * assignment.PAISE_PER_PORTION_KM // 1000 * a.portions
            metres += c.metres * a.portions
            used.add(a.kitchen_id)
        if answer.summary is not None:
            earnings = {e.kitchen_id: e.earnings_paise for e in answer.summary.earnings_paise}
    week_status = next((s for s in statuses if s != "solved"), "solved")
    each = max(portions, 1)
    return SweepPoint(
        fairness_lambda=fairness_lambda,
        status=week_status,
        evening_status=statuses,
        greedy_evenings=greedy_evenings,
        seconds=round(seconds, 3),
        gini=assignment.gini([earnings[k] for k in sorted(eligible)]),
        gini_all=assignment.gini(list(earnings.values())),
        cost_paise_per_portion=round(cost / each, 2),
        rider_paise_per_portion=round(rider / each, 2),
        km_per_portion=round(metres / 1000 / each, 3),
        kitchens_used=len(used),
    )


def run_sweep(seed: int, catalog: Catalog, *, adoption: float | None) -> SweepRun | None:
    week = instance(seed, catalog, adoption)
    if week is None:
        return None
    points = [run_week(week, lam) for lam in (None, *LAMBDAS)]
    days = sum(1 for e in week.evenings if e)
    return SweepRun(seed, days, week.orders, week.portions, points)


METRICS = (
    "gini",
    "gini_all",
    "cost_paise_per_portion",
    "rider_paise_per_portion",
    "km_per_portion",
)


def paired_runs(runs: list[SweepRun]) -> tuple[list[SweepRun], list[int]]:
    """The seeds where every point solved, and the seeds dropped (for every point) because one
    did not."""
    kept = [r for r in runs if r.solved]
    return kept, sorted(r.seed for r in runs if not r.solved)


def extra_cost(run: SweepRun, fairness_lambda: float) -> float:
    """cost_λ / cost_greedy - 1 within a seed. Greedy's cost is positive: every kitchen is rated
    below 5, so each portion carries a quality penalty."""
    point, greedy = run.point(fairness_lambda), run.point(None)
    assert point is not None and greedy is not None
    return point.cost_paise_per_portion / greedy.cost_paise_per_portion - 1


def summarise(runs: list[SweepRun]) -> dict[str, dict[str, Estimate]]:
    """λ (or "greedy") → each metric's mean ± CI across the paired seeds; for a λ also
    `extra_cost`, the per-seed cost against greedy."""
    kept, _ = paired_runs(runs)
    out: dict[str, dict[str, Estimate]] = {}
    if not kept:
        return out
    for lam in (None, *LAMBDAS):
        points = [p for r in kept if (p := r.point(lam)) is not None]
        out[key(lam)] = {m: estimate([getattr(p, m) for p in points]) for m in METRICS}
        if lam is not None:
            out[key(lam)]["extra_cost"] = estimate([extra_cost(r, lam) for r in kept])
    return out


def _upper(e: Estimate) -> float:
    return e.mean + e.ci


def against_target(runs: list[SweepRun]) -> dict[str, Any]:
    """M-G: Gini ≤ 0.25 at ≤ +5 % cost against greedy. A λ holds when, over the paired seeds,
    the 95 % CI upper bound of cost_λ / cost_greedy - 1 is ≤ 0.05 and that of Gini_λ is ≤ 0.25.
    λ = 0 is the cost-only plan and cannot meet the fairness target. No verdict (None) with
    fewer than 5 paired seeds."""
    if not runs:
        return {}
    kept, dropped = paired_runs(runs)
    n = len(kept)
    per_lambda: dict[str, Any] = {}
    for lam in LAMBDAS:
        if not kept:
            break
        extra = estimate([extra_cost(r, lam) for r in kept])
        gini = estimate([p.gini for r in kept if (p := r.point(lam)) is not None])
        within = lam > 0 and _upper(extra) <= COST_LIMIT and _upper(gini) <= GINI_LIMIT
        per_lambda[key(lam)] = {
            "extra_cost": extra.as_dict(),
            "extra_cost_upper": round(_upper(extra), 4),
            "gini": gini.as_dict(),
            "gini_upper": round(_upper(gini), 4),
            "eligible": lam > 0,
            "holds": None if n < MIN_SEEDS else within,
        }
    holding = [float(k) for k, v in per_lambda.items() if v["holds"]]
    greedy = estimate([p.gini for r in kept if (p := r.point(None)) is not None])
    return {
        "gini_le_0_25_at_le_5pct_cost": {
            "rule": "per-seed paired: 95 % CI upper bounds of cost_λ / cost_greedy - 1 ≤ 0.05 "
            "and of the weekly Gini ≤ 0.25, λ > 0",
            "holds": None if n < MIN_SEEDS else bool(holding),
            "lambda": min(holding) if holding else None,
            "n": n,
            "seeds": [r.seed for r in kept],
            "dropped_seeds": dropped,
            "greedy_gini": greedy.as_dict(),
            "per_lambda": per_lambda,
        }
    }
