"""RQ4: what fairness costs in home-kitchen assignment (eval protocol §4 and §5, M-G).

One evening per seed: the simulated households that take a tiffin (no-cook Sunday) order
portions at their society; a handful of kitchens around the hub, each with a rolling earnings
balance, cook them. The λ sweep runs the assignment model at each λ and reports the Gini of
the kitchens' earnings against ₹ per portion; greedy nearest kitchen is the baseline. The
target (hypothesis): Gini ≤ 0.25 at ≤ +5 % cost against greedy.
"""

import random
from dataclasses import dataclass
from typing import Any

from gharbasket_ml import assignment
from gharbasket_ml.schemas import AssignRequest
from gharbasket_sim import config
from gharbasket_sim.catalog import Catalog
from gharbasket_sim.households import sample

from gharbasket.tools.seed import SOCIETIES
from gharbasket_training.eval.stats import Estimate, estimate

LAMBDAS = (0.0, 0.25, 0.5, 1.0, 2.0, 4.0)
HUB = (19.0300, 73.0690)  # the Kharghar hub (spike S-3)
KITCHENS = 6


@dataclass(frozen=True)
class SweepPoint:
    fairness_lambda: float | None  # None = greedy
    gini: float
    cost_paise_per_portion: float
    km_per_portion: float
    kitchens_used: int


@dataclass(frozen=True)
class SweepRun:
    seed: int
    orders: int
    portions: int
    points: list[SweepPoint]


def _centroid(boundary: list[tuple[float, float]]) -> tuple[float, float]:
    lat = sum(p[0] for p in boundary) / len(boundary)
    lng = sum(p[1] for p in boundary) / len(boundary)
    return lat, lng


def instance(
    seed: int, catalog: Catalog, adoption: float | None, fairness_lambda: float
) -> AssignRequest | None:
    """Tomorrow's tiffin orders and the kitchens, from the simulated households."""
    rng = random.Random(f"gharbasket-eval-assign:{seed}")  # noqa: S311 (a seeded instance)
    centres = {str(s.id): _centroid(s.boundary) for s in SOCIETIES if s.boundary}
    orders = []
    for h in sample(seed, config.load(), catalog, adoption):
        if 6 not in h.rules["no_cook_days"] or h.society_id not in centres:
            continue
        lat, lng = centres[h.society_id]
        skills = ["veg", "jain"] if h.rules["jain"] else ["veg"]
        orders.append(
            {
                "id": h.key,
                "lat": lat + rng.uniform(-0.0005, 0.0005),
                "lng": lng + rng.uniform(-0.0005, 0.0005),
                "portions": h.rules["size"],
                "skills": skills,
            }
        )
    if not orders:
        return None
    portions = sum(o["portions"] for o in orders)
    kitchens = [
        {
            "id": f"kitchen-{k}",
            "lat": HUB[0] + rng.uniform(-0.03, 0.03),
            "lng": HUB[1] + rng.uniform(-0.04, 0.04),
            "max_portions": max(8, portions // 3),
            "skills": ["veg", "jain"] if k % 2 == 0 else ["veg"],
            "rating": round(rng.uniform(3.9, 4.9), 1),
            "earnings_paise": rng.choice([0, 60_000, 120_000, 240_000, 360_000]),
        }
        for k in range(KITCHENS)
    ]
    return AssignRequest.model_validate(
        {
            "orders": orders,
            "kitchens": kitchens,
            "fairness_lambda": fairness_lambda,
            "max_km": 8.0,
            "time_limit_s": 2.0,
        }
    )


def run_sweep(seed: int, catalog: Catalog, *, adoption: float | None) -> SweepRun | None:
    base = instance(seed, catalog, adoption, 0.0)
    if base is None:
        return None
    points = []
    greedy = assignment.baseline(base)
    if greedy.summary is not None:
        s = greedy.summary
        points.append(
            SweepPoint(None, s.gini, s.cost_paise_per_portion, s.km_per_portion, s.kitchens_used)
        )
    for lam in LAMBDAS:
        req = instance(seed, catalog, adoption, lam)
        assert req is not None
        answer = assignment.assign(req)
        if answer.summary is None:
            continue
        s = answer.summary
        points.append(
            SweepPoint(lam, s.gini, s.cost_paise_per_portion, s.km_per_portion, s.kitchens_used)
        )
    return SweepRun(seed, len(base.orders), sum(o.portions for o in base.orders), points)


def summarise(runs: list[SweepRun]) -> dict[str, dict[str, Estimate]]:
    """λ (or "greedy") → gini / cost / km, mean ± CI across seeds."""
    out: dict[str, dict[str, Estimate]] = {}
    keys = ["greedy", *(str(lam) for lam in LAMBDAS)]
    for key in keys:
        lam = None if key == "greedy" else float(key)
        mine = [p for r in runs for p in r.points if p.fairness_lambda == lam]
        if not mine:
            continue
        out[key] = {
            "gini": estimate([p.gini for p in mine]),
            "cost_paise_per_portion": estimate([p.cost_paise_per_portion for p in mine]),
            "km_per_portion": estimate([p.km_per_portion for p in mine]),
        }
    return out


def against_target(summary: dict[str, dict[str, Estimate]]) -> dict[str, Any]:
    greedy = summary.get("greedy")
    if greedy is None:
        return {}
    best = None
    for key, metrics in summary.items():
        if key == "greedy":
            continue
        extra = (
            metrics["cost_paise_per_portion"].mean / max(greedy["cost_paise_per_portion"].mean, 1)
            - 1
        )
        if metrics["gini"].mean <= 0.25 and extra <= 0.05 and (best is None or float(key) < best):
            best = float(key)
    return {
        "gini_le_0_25_at_le_5pct_cost": {
            "value": best if best is not None else -1.0,
            "holds": best is not None,
            "greedy_gini": round(greedy["gini"].mean, 3),
        }
    }
