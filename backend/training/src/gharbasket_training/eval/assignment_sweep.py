"""RQ4: what fairness costs in home-kitchen assignment (eval protocol §4, §5 and §7, M-G).

The harness lives the evenings the way production assigns them (M8 §3.4, `partners.assignments`).
Per seed, `weeks` consecutive simulated weeks (default 5): the simulated households that take
tiffins (those with a no-cook day) order dinner portions at their society on each evening, every
one of them on its no-cook day (Sunday in the simulator) and on each other evening with
probability `WEEKDAY_TIFFIN_SHARE` (the simulator has no weekday tiffin demand yet, so the harness
draws it). Each order is booked from a kitchen's menu, so it carries that kitchen as its preferred
cook (ADR-0027 §7): the household's favourite, drawn once per seed among the kitchens that can cook
its tiffin. `kitchens` home kitchens around the hub cook them.

Each evening, as in production: the preferred cook takes her orders first while she is eligible
and has room (`matching.preferred_first`, the production function), then the rest goes to the
assignment with every kitchen's room left and her **rolling 28-day earnings** (the evenings before
tonight in the window, plus what the preferred orders gave her tonight), within 5 km. The first
four weeks fill the 28-day window; the last week is scored, so each scored evening sees a full
window.

Per seed, the greedy nearest kitchen and the CP-SAT model at every λ live the same weeks; the
scored week is judged by the Gini of the kitchens' earnings in that week and the cost a portion
(distance + quality penalty) over all its assigned portions, the preferred cooks' included. An
order no kitchen in reach has room for is left out, as production refunds it (ADR-0027), and the
share left out is reported. A seed is the pair (dropped when CP-SAT fell back to greedy on any
evening): the M-G target (Gini ≤ 0.25 at ≤ +5 % cost against greedy) is judged on the per-seed
cost_λ / cost_greedy - 1 and Gini_λ, mean ± 95 % t-CI.
"""

import hashlib
import importlib.metadata
import random
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gharbasket_ml import assignment
from gharbasket_ml.routing import haversine_m
from gharbasket_ml.schemas import AssignRequest, AssignResponse
from gharbasket_sim import config
from gharbasket_sim.catalog import Catalog
from gharbasket_sim.households import sample

from gharbasket.modules.partners import matching
from gharbasket.tools import seed as world
from gharbasket.tools.seed import SOCIETIES
from gharbasket_training.eval.stats import Estimate, estimate

# Eval protocol §5: paise of cost per portion of imbalance (M10 §3.5). The registered grid, and
# the refined grid of 400-1200 in steps of 100 between its 300 and 2000 (§7, 2026-11-01).
PROTOCOL_LAMBDAS = (0.0, 100.0, 200.0, 300.0, 500.0, 700.0, 1000.0, 2000.0)
REFINED_LAMBDAS = tuple(float(x) for x in range(400, 1201, 100))
LAMBDAS = tuple(sorted({*PROTOCOL_LAMBDAS, *REFINED_LAMBDAS}))
HUB = (19.0300, 73.0690)  # the Kharghar hub (spike S-3)
KITCHENS = 6
WEEKS = 5  # four weeks fill the 28-day window, the fifth is scored
EVENINGS = 7  # Monday to Sunday: a week
WEEKDAY_TIFFIN_SHARE = 0.5  # a tiffin household's chance of ordering on an evening it cooks
PREFERRED_SHARE = 1.0  # orders that name a preferred cook: all, every tiffin is booked from a menu
# Production's numbers (M8 §3.4, `partners.assignments` and `partners.matching`; a test pins them).
ROLLING_DAYS = 28  # `assignments.ROLLING`: the window of the earnings the fairness term sees
TIME_LIMIT_S = 5.0  # `assignments.TIME_LIMIT_S`, here as deterministic time (`deterministic=True`)
MAX_KM = matching.MAX_KM
RATING_FLOOR = matching.RATING_FLOOR
RATE = matching.PAISE_PER_PORTION  # what a kitchen earns a portion (the default rate card)
COST_LIMIT = 0.05  # the target: at most +5 % cost a portion against greedy ...
GINI_LIMIT = 0.25  # ... at a Gini of weekly earnings of at most 0.25
MIN_SEEDS = 5  # the protocol's seeds; fewer give no verdict


@dataclass(frozen=True)
class SweepConfig:
    """What a sweep varies (the CLI's `--assign-*` flags); the defaults are the protocol's."""

    lambdas: tuple[float, ...] = LAMBDAS
    weeks: int = WEEKS
    kitchens: int = KITCHENS
    preferred_share: float = PREFERRED_SHARE


@dataclass(frozen=True)
class Weeks:
    """A seed's weeks: each evening's tiffin orders (`TiffinOrderIn` fields plus `preferred`, the
    kitchen id of the booked menu or None) and the kitchens (`KitchenIn` fields without capacity or
    earnings, which change by the evening). The last `EVENINGS` evenings are the scored week."""

    evenings: list[list[dict[str, Any]]]
    kitchens: list[dict[str, Any]]

    @property
    def scored(self) -> list[list[dict[str, Any]]]:
        return self.evenings[-EVENINGS:]

    @property
    def orders(self) -> int:
        return sum(len(e) for e in self.scored)

    @property
    def portions(self) -> int:
        return sum(o["portions"] for e in self.scored for o in e)


@dataclass(frozen=True)
class SweepPoint:
    fairness_lambda: float | None  # None = greedy nearest kitchen
    # Every evening's status, the warm-up weeks included ("solved" also when every order went to
    # its preferred cook), and the first that is not "solved" ("partial": some order no kitchen in
    # reach had room for, left out as production refunds it; "infeasible": none of the rest).
    status: str
    evening_status: list[str]
    # Evenings greedy answered although some kitchen could take an order (for a λ: CP-SAT fell
    # back); an evening whose rest no kitchen can take has nothing to solve and is not counted.
    greedy_evenings: int
    seconds: float  # the solve time over every evening
    gini: float  # of the scored week's earnings, over the kitchens eligible on some evening of it
    gini_all: float  # of the scored week's earnings, over every kitchen
    cost_paise_per_portion: float  # distance + quality penalty, over the scored week's portions
    rider_paise_per_portion: float  # the distance part alone
    km_per_portion: float
    kitchens_used: int
    preferred_share: float = 0.0  # the scored week's portions the preferred cooks took first
    gini_rolling: float = 0.0  # of the 28-day earnings after the last evening (what λ balances)
    unassigned_share: float = 0.0  # of the scored week's ordered portions, left out

    @property
    def solved(self) -> bool:
        """For a λ: CP-SAT answered every evening it had something to solve. Orders left out
        for want of a kitchen in reach with room are not a solver failure: they are reported
        (`unassigned_share`), and the per-portion figures count the assigned portions."""
        return self.fairness_lambda is None or self.greedy_evenings == 0


@dataclass(frozen=True)
class SweepRun:
    seed: int
    evenings: int  # of the scored week, with at least one order
    orders: int  # of the scored week
    portions: int  # of the scored week
    points: list[SweepPoint]
    config: SweepConfig = field(default_factory=SweepConfig)

    @property
    def solved(self) -> bool:
        """Every point solved: only then is the seed a pair for every λ."""
        return all(p.solved for p in self.points)

    def point(self, fairness_lambda: float | None) -> SweepPoint | None:
        return next((p for p in self.points if p.fairness_lambda == fairness_lambda), None)


def key(fairness_lambda: float | None) -> str:
    return "greedy" if fairness_lambda is None else f"{fairness_lambda:g}"


def settings(cfg: SweepConfig | None = None) -> dict[str, Any]:
    """What the sweep's numbers came from, for the report's provenance."""
    cfg = cfg or SweepConfig()
    fields = AssignRequest.model_fields
    seed_file = Path(world.__file__)
    return {
        "lambdas": list(cfg.lambdas),
        "protocol_lambdas": list(PROTOCOL_LAMBDAS),
        "kitchens": cfg.kitchens,
        "hub": list(HUB),
        "weeks": cfg.weeks,
        "scored_week": "the last",
        "evenings_per_week": EVENINGS,
        "rolling_days": ROLLING_DAYS,
        "weekday_tiffin_share": WEEKDAY_TIFFIN_SHARE,
        "preferred_share": cfg.preferred_share,
        "preferred_cook": "the household's favourite, drawn once per seed uniformly among the "
        "kitchens whose skills cover its tiffin; matching.preferred_first before the solver",
        "kitchen_capacity": "max(8, the evening's portions // 3)",
        "max_km": MAX_KM,
        "rating_floor": RATING_FLOOR,
        "paise_per_portion": RATE,
        "time_limit_s": TIME_LIMIT_S,
        "deterministic": True,
        "quality_paise_per_star": fields["quality_paise_per_star"].default,
        "rider_paise_per_portion_km": assignment.PAISE_PER_PORTION_KM,
        "max_kitchens_per_order": fields["max_kitchens_per_order"].default,
        "model": assignment.MODEL,
        "baseline": assignment.GREEDY,
        "ortools_version": importlib.metadata.version("ortools"),
        "societies_source": "gharbasket.tools.seed.SOCIETIES",
        "societies_sha256": hashlib.sha256(seed_file.read_bytes()).hexdigest(),
        "gini_population": "kitchens eligible on at least one evening of the scored week",
        "cost_population": "every assigned portion of the scored week, the preferred cooks' "
        "included",
        "pairing": "a seed is dropped when CP-SAT fell back to greedy on any evening of any week "
        "at any λ; orders no kitchen in reach has room for are left out in every arm, as "
        "production refunds them (ADR-0027), and reported as unassigned_share",
    }


def _centroid(boundary: list[tuple[float, float]]) -> tuple[float, float]:
    lat = sum(p[0] for p in boundary) / len(boundary)
    lng = sum(p[1] for p in boundary) / len(boundary)
    return lat, lng


def _id(name: str) -> str:
    """A stable uuid for a harness name: `matching` works on uuids, as production does."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"gharbasket-eval-assign:{name}"))


def kitchens_for(seed: int, count: int) -> list[dict[str, Any]]:
    """`count` kitchens around the hub; the first n are the same for any count ≥ n."""
    rng = random.Random(f"gharbasket-eval-assign:{seed}")  # noqa: S311 (seeded)
    return [
        {
            "id": _id(f"{seed}:kitchen-{k}"),
            "lat": HUB[0] + rng.uniform(-0.03, 0.03),
            "lng": HUB[1] + rng.uniform(-0.04, 0.04),
            "skills": ["veg", "jain"] if k % 2 == 0 else ["veg"],
            "rating": round(rng.uniform(3.9, 4.9), 1),
        }
        for k in range(count)
    ]


def instance(
    seed: int, catalog: Catalog, adoption: float | None, cfg: SweepConfig | None = None
) -> Weeks | None:
    """`cfg.weeks` weeks of tiffin orders from the simulated households, and the kitchens."""
    cfg = cfg or SweepConfig()
    centres = {str(s.id): _centroid(s.boundary) for s in SOCIETIES if s.boundary}
    takers = [
        h
        for h in sample(seed, config.load(), catalog, adoption)
        if h.rules["no_cook_days"] and h.society_id in centres
    ]
    kitchens = kitchens_for(seed, cfg.kitchens)
    favourite: dict[str, str | None] = {}
    for h in takers:
        need = {"veg", "jain"} if h.rules["jain"] else {"veg"}
        can = [k["id"] for k in kitchens if need <= set(k["skills"])]
        rng = random.Random(f"gharbasket-eval-assign:{seed}:cook:{h.key}")  # noqa: S311 (seeded)
        favourite[h.key] = rng.choice(can) if can else None
    evenings: list[list[dict[str, Any]]] = []
    for day in range(cfg.weeks * EVENINGS):
        rng = random.Random(f"gharbasket-eval-assign:{seed}:{day}")  # noqa: S311 (seeded)
        menu = random.Random(f"gharbasket-eval-assign:{seed}:menu:{day}")  # noqa: S311 (seeded)
        tonight = []
        for h in takers:
            draw = rng.random()  # one draw per household and evening, ordered or not
            if day % EVENINGS not in h.rules["no_cook_days"] and draw >= WEEKDAY_TIFFIN_SHARE:
                continue
            lat, lng = centres[h.society_id]
            booked = menu.random() < cfg.preferred_share
            tonight.append(
                {
                    "id": _id(f"{seed}:{h.key}-d{day}"),
                    "lat": lat + rng.uniform(-0.0005, 0.0005),
                    "lng": lng + rng.uniform(-0.0005, 0.0005),
                    "portions": h.rules["size"],
                    "skills": ["veg", "jain"] if h.rules["jain"] else ["veg"],
                    "preferred": favourite[h.key] if booked else None,
                }
            )
        evenings.append(tonight)
    if not any(evenings[-EVENINGS:]):
        return None
    return Weeks(evenings, kitchens)


def _cost(order: dict[str, Any], kitchen: dict[str, Any]) -> tuple[int, int]:
    """Metres and the cost a portion of a pair, as `assignment.candidates` counts them."""
    metres = round(haversine_m((order["lat"], order["lng"]), (kitchen["lat"], kitchen["lng"])))
    quality = round(
        (5.0 - kitchen["rating"]) * AssignRequest.model_fields["quality_paise_per_star"].default
    )
    return metres, metres * assignment.PAISE_PER_PORTION_KM // 1000 + quality


@dataclass(frozen=True)
class Evening:
    """One evening's outcome: (order id, kitchen id, portions) pairs, who took them first."""

    matches: list[tuple[str, str, int]]
    preferred: int  # portions the preferred cooks took
    eligible: set[str]  # kitchens that could take at least one of tonight's orders
    status: str
    greedy: bool  # greedy answered although some kitchen could take an order
    seconds: float


def evening(
    orders: list[dict[str, Any]],
    kitchens: list[dict[str, Any]],
    rolling: dict[str, int],
    fairness_lambda: float | None,
) -> Evening:
    """One evening as M8's round runs it (`assignments._meal_round`): the preferred cooks first,
    then one request with every kitchen's room left and rolling earnings (None: greedy)."""
    capacity = max(8, sum(o["portions"] for o in orders) // 3)
    cooks = [
        matching.Cook(
            uuid.UUID(k["id"]),
            k["lat"],
            k["lng"],
            capacity,
            frozenset(k["skills"]),
            k["rating"],
            rolling[k["id"]],
        )
        for k in kitchens
    ]
    wanted = [
        matching.Order(
            uuid.UUID(o["id"]),
            o["lat"],
            o["lng"],
            o["portions"],
            frozenset(o["skills"]),
            uuid.UUID(o["preferred"]) if o.get("preferred") else None,
        )
        for o in orders
    ]
    eligible = {str(i) for i in matching.eligible_ids(wanted, cooks)}
    first, rest = matching.preferred_first(wanted, cooks, RATE)
    matches = [(str(m.order_id), str(m.partner_id), m.portions) for m in first]
    preferred = sum(m.portions for m in first)
    if not rest:  # every order went to its preferred cook ("preferred-m8", solved)
        return Evening(matches, preferred, eligible, "solved", False, 0.0)
    req = AssignRequest.model_validate(
        {
            "orders": [
                {"id": str(o.id), "lat": o.lat, "lng": o.lng, "portions": o.portions}
                | {"skills": sorted(o.skills)}
                for o in rest
            ],
            "kitchens": [
                {"id": str(c.id), "lat": c.lat, "lng": c.lng, "max_portions": c.left}
                | {"skills": sorted(c.skills), "rating": c.rating, "earnings_paise": c.earnings}
                for c in cooks
            ],
            "fairness_lambda": fairness_lambda or 0.0,
            "rating_floor": RATING_FLOOR,
            "max_km": MAX_KM,
            "paise_per_portion": RATE,
            "time_limit_s": TIME_LIMIT_S,
        }
    )
    answer: AssignResponse = (
        assignment.baseline(req)
        if fairness_lambda is None
        else assignment.assign(req, deterministic=True)
    )
    status = answer.status
    if fairness_lambda is None and status == "fallback":
        status = "solved"  # greedy is the baseline's own answer, every order assigned
    matches += [(a.order_id, a.kitchen_id, a.portions) for a in answer.assignments]
    greedy = answer.model == assignment.GREEDY and bool(assignment.candidates(req))
    return Evening(matches, preferred, eligible, status, greedy, answer.seconds)


def run_weeks(weeks: Weeks, fairness_lambda: float | None) -> SweepPoint:
    """Every evening of the weeks at one λ (None: greedy), each seeing the kitchens' rolling
    earnings of the `ROLLING_DAYS` evenings before it; the last week is scored."""
    ids = [k["id"] for k in weeks.kitchens]
    by_id = {k["id"]: k for k in weeks.kitchens}
    scored_from = len(weeks.evenings) - EVENINGS
    daily: list[dict[str, int]] = []
    eligible: set[str] = set()
    used: set[str] = set()
    statuses: list[str] = []
    greedy_evenings = 0
    seconds = 0.0
    ordered = portions = preferred = cost = rider = metres = 0
    for day, orders in enumerate(weeks.evenings):
        earned = dict.fromkeys(ids, 0)
        daily.append(earned)
        if not orders:
            continue
        window = daily[max(0, day - ROLLING_DAYS) : day]
        rolling = {k: sum(d[k] for d in window) for k in ids}
        night = evening(orders, weeks.kitchens, rolling, fairness_lambda)
        statuses.append(night.status)
        greedy_evenings += night.greedy
        seconds += night.seconds
        by_order = {o["id"]: o for o in orders}
        for _, kitchen_id, n in night.matches:
            earned[kitchen_id] += n * RATE
        if day < scored_from:
            continue
        eligible |= night.eligible
        preferred += night.preferred
        ordered += sum(o["portions"] for o in orders)
        for order_id, kitchen_id, n in night.matches:
            m, c = _cost(by_order[order_id], by_id[kitchen_id])
            portions += n
            cost += c * n
            rider += m * assignment.PAISE_PER_PORTION_KM // 1000 * n
            metres += m * n
            used.add(kitchen_id)
    week = {k: sum(d[k] for d in daily[scored_from:]) for k in ids}
    last = {k: sum(d[k] for d in daily[-ROLLING_DAYS:]) for k in ids}
    each = max(portions, 1)
    return SweepPoint(
        fairness_lambda=fairness_lambda,
        status=next((s for s in statuses if s != "solved"), "solved"),
        evening_status=statuses,
        greedy_evenings=greedy_evenings,
        seconds=round(seconds, 3),
        gini=assignment.gini([week[k] for k in sorted(eligible)]),
        gini_all=assignment.gini(list(week.values())),
        cost_paise_per_portion=round(cost / each, 2),
        rider_paise_per_portion=round(rider / each, 2),
        km_per_portion=round(metres / 1000 / each, 3),
        kitchens_used=len(used),
        preferred_share=round(preferred / each, 4),
        gini_rolling=assignment.gini(list(last.values())),
        unassigned_share=round(1 - portions / ordered, 4) if ordered else 0.0,
    )


def run_sweep(
    seed: int, catalog: Catalog, *, adoption: float | None, cfg: SweepConfig | None = None
) -> SweepRun | None:
    cfg = cfg or SweepConfig()
    weeks = instance(seed, catalog, adoption, cfg)
    if weeks is None:
        return None
    points = [run_weeks(weeks, lam) for lam in (None, *cfg.lambdas)]
    days = sum(1 for e in weeks.scored if e)
    return SweepRun(seed, days, weeks.orders, weeks.portions, points, cfg)


METRICS = (
    "gini",
    "gini_all",
    "gini_rolling",
    "cost_paise_per_portion",
    "rider_paise_per_portion",
    "km_per_portion",
    "preferred_share",
    "unassigned_share",
)


def lambdas_of(runs: list[SweepRun]) -> tuple[float, ...]:
    return runs[0].config.lambdas if runs else LAMBDAS


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
    for lam in (None, *lambdas_of(runs)):
        points = [p for r in kept if (p := r.point(lam)) is not None]
        out[key(lam)] = {m: estimate([getattr(p, m) for p in points]) for m in METRICS}
        if lam is not None:
            out[key(lam)]["extra_cost"] = estimate([extra_cost(r, lam) for r in kept])
    return out


def _upper(e: Estimate) -> float:
    return e.mean + e.ci


KNEE_RULE = (
    "the target holds: the smallest λ > 0 that meets it (§4); it does not: the knee, the "
    "largest λ > 0 of the grid up to which every λ > 0 keeps the mean cost against greedy "
    "≤ +5 % (§7, 2026-11-01)"
)


def production_lambda(per_lambda: dict[str, Any], holds: bool | None) -> float | None:
    """The λ production runs at, by the rule fixed before the sweep (eval protocol §7,
    2026-11-01). When the target holds, the protocol's own pick: the smallest λ > 0 meeting it.
    When it does not, the knee: walking up the grid from the smallest λ > 0, the last λ before
    the first whose mean extra cost is above +5 % (the most fairness the cost limit buys on
    average). None with no verdict (fewer than 5 paired seeds), or when even the smallest λ > 0
    costs more than that."""
    if holds is None:
        return None
    grid = sorted((float(k), v) for k, v in per_lambda.items() if float(k) > 0)
    if holds:
        return min(lam for lam, v in grid if v["holds"])
    knee = None
    for lam, v in grid:
        if v["extra_cost"]["mean"] > COST_LIMIT:
            break
        knee = lam
    return knee


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
    for lam in lambdas_of(runs):
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
    holds = None if n < MIN_SEEDS else bool(holding)
    greedy = estimate([p.gini for r in kept if (p := r.point(None)) is not None])
    return {
        "gini_le_0_25_at_le_5pct_cost": {
            "rule": "per-seed paired: 95 % CI upper bounds of cost_λ / cost_greedy - 1 ≤ 0.05 "
            "and of the weekly Gini ≤ 0.25, λ > 0",
            "holds": holds,
            "lambda": min(holding) if holding else None,
            "n": n,
            "seeds": [r.seed for r in kept],
            "dropped_seeds": dropped,
            "greedy_gini": greedy.as_dict(),
            "per_lambda": per_lambda,
            "production_lambda": production_lambda(per_lambda, holds),
            "production_rule": KNEE_RULE,
        }
    }
