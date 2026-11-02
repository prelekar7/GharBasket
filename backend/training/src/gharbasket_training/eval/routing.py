"""M-F: van routing against nearest neighbour on seeded delivery mornings (eval protocol §4, §7
2026-11-02; handbook 08 M-F).

**The mornings** port spike S-3's seeded layout (`spikes/s3-routing/route.py`) to haversine
distances, the matrix production uses when OSRM is down and the only one staging has: the Kharghar
hub, the five seed societies with a hub (`gharbasket.tools.seed.SOCIETIES`, their boundary's
centre), then random points in Kharghar, Kamothe, Kalamboli, Khandeshwar, New Panvel and Belapur
(not snapped to roads: no OSRM). Each society gets 4-20 orders of 3-6 kg, M7's crates (three bags
to a crate), its empties back on 70 % of societies, and the captain's window 07-09 or 08-10, the
two box waves (S-3; M7 §3.2 sends one stop per society and slot). The request is the one M7 builds
(`logistics.solver`): vans loaded by 06:30, back within 90 minutes of the latest window's end,
`time_limit_s` 20 (two waves of 10 s), no food bank (M7 sends none; donations ride back to the
hub), and the M10 defaults for vans, service and cost. The fleet is S-3's 1.5 x the vans the
crates need (M7 sends the hub's active fleet, which the simulator does not have). `morning()` is
the reusable generator (M-H's slot-pricing eval, M10 §3.8, expects these fixtures); with
`food_bank=True` it adds S-3's donation pickups and the CBD Belapur food bank.

**The arms**, both production code on the same matrix and both scored by production's
`routing.check()`: `routing.plan()` (the two-wave CVRPTW of `POST /route/solve`, with the time
limit M7 sends) and `routing.nearest_neighbour()` (the baseline and the service's fallback).
OR-Tools' routing library has no deterministic time limit, so the plan depends on the machine's
speed: the wall time is recorded, and guided local search spends its whole budget by design.

**The verdict** (fixed before the first run, §7 2026-11-02): per seed and size, ₹ per served order
and on-time share; the M-F target holds at a size when, over at least 5 seeds, the 95 % t-CI
**lower** bound of the plan's on-time share is ≥ 0.95, the **upper** bound of the per-seed paired
cost_plan / cost_nn - 1 (per served order) is ≤ -0.10, and every plan is the solver's (not the
fallback) with no checker problem. The guardrail (≤ 10 s a wave) is judged on the setting, since
the solver always spends its budget: the time limit a wave (half of `time_limit_s`, one wave or
two) is ≤ 10 s; the wall time over it (the overhead) is reported, and the plan is also solved at a
shorter budget (2 s a wave, `SHORT_LIMIT_S`) as evidence that the budget is enough (review of PR
#475). The headline size is 50 societies (S-3's, and the handbook's figure); 100 and 200 show
scale (opt-in).
"""

import argparse
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import random
import sys
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from html import escape as html_escape
from pathlib import Path
from typing import Any

from gharbasket_ml import routing
from gharbasket_ml.schemas import RouteRequest, VansIn

from gharbasket.tools import seed as world
from gharbasket.tools.seed import SOCIETIES
from gharbasket_training.eval.stats import estimate

HUB = (19.0300, 73.0690)  # spike S-3's Kharghar hub (and the λ sweep's)
FOOD_BANK = (19.0190, 73.0390)  # S-3's food bank in CBD Belapur (only with `food_bank=True`)
FOOD_BANK_WINDOW = (8 * 3600, 12 * 3600)
AREA = (18.995, 19.055, 73.035, 73.125)  # lat, lat, lng, lng: S-3's ~9 x 10 km
WINDOWS = ((7 * 3600, 9 * 3600), (8 * 3600, 10 * 3600))  # the two box waves, 07-09 and 08-10
# Production's numbers (M7 `logistics.solver`, M6 §3.1; a test pins them).
DEPART_S = 6 * 3600 + 30 * 60  # the hub's dispatch, 06:30
RETURN_MARGIN_S = 90 * 60  # `solver.RETURN_MARGIN_S`: back within 90 min of the latest window
TIME_LIMIT_S = 20.0  # `solver.TIME_LIMIT_S`: both waves together, 10 s each
BAGS_PER_CRATE = 3  # `solver.BAGS_PER_CRATE`: one bag an order (M6)
# S-3's assumptions (spike note "Setup"; pilot data replaces them).
ORDERS = (4, 20)  # per society
KG_TENTHS_PER_ORDER = (30, 60)  # 3 to 6 kg an order
EMPTIES_SHARE = 0.7  # societies served yesterday hand back their empties
FLEET_SLACK = 1.5  # vans available = 1.5 x the vans the crates need
MAX_VANS = 60  # `VansIn.count`'s ceiling
SIZES = (50,)  # the default: the headline; scale is opt-in (`--routing-sizes 50 100 200`)
SCALE_SIZES = (100, 200)
HEADLINE_SIZE = 50
SHORT_LIMIT_S = 4.0  # the shorter budget, 2 s a wave: is 10 s a wave enough?
MAX_SIZE = 200  # 1.5 x the crates of 200 societies is about 55 vans, under MAX_VANS
# The M-F target and guardrail (eval protocol §4).
ON_TIME_TARGET = 0.95
SAVING_TARGET = 0.10  # ₹/order at least 10 % below nearest neighbour
WAVE_LIMIT_S = 10.0
MIN_SEEDS = 5
ARMS = ("cvrptw_waves", "nearest_neighbour")
SHORT_ARM = "cvrptw_waves_short"  # the plan at `SHORT_LIMIT_S`, reference only


def _centre(boundary: list[tuple[float, float]]) -> tuple[float, float]:
    return sum(p[0] for p in boundary) / len(boundary), sum(p[1] for p in boundary) / len(boundary)


def seed_societies() -> list[tuple[str, float, float]]:
    """The seed world's societies with a hub and a boundary: (slug, lat, lng) of the centre."""
    return [(s.slug, *_centre(s.boundary)) for s in SOCIETIES if s.boundary and s.hub_id]


def morning(
    seed: int,
    size: int,
    *,
    food_bank: bool = False,
    hub: tuple[float, float] = HUB,
    windows: tuple[tuple[int, int], ...] = WINDOWS,
    fleet_slack: float = FLEET_SLACK,
) -> RouteRequest:
    """One delivery morning of `size` societies, as M7 would send it to `POST /route/solve`.
    Deterministic in (seed, size) for the same keywords; `food_bank` adds S-3's donations
    (size // 6 societies); `hub`, `windows` and `fleet_slack` default to S-3's (M-H reuses this)."""
    if not 1 <= size <= MAX_SIZE:
        raise ValueError(f"size must be within 1 to {MAX_SIZE}")
    rng = random.Random(f"gharbasket-eval-routing:{seed}:{size}")  # noqa: S311 (seeded)
    places = seed_societies()[:size]
    while len(places) < size:
        lat, lng = rng.uniform(AREA[0], AREA[1]), rng.uniform(AREA[2], AREA[3])
        places.append((f"society-{len(places) + 1}", lat, lng))
    stops: list[dict[str, Any]] = []
    for name, lat, lng in places:
        orders = rng.randint(*ORDERS)
        crates = math.ceil(orders / BAGS_PER_CRATE)
        start, end = rng.choice(windows)
        stops.append(
            {
                "id": f"{name}/{start // 3600:02d}",
                "lat": round(lat, 6),
                "lng": round(lng, 6),
                "orders": orders,
                "kg_tenths": sum(rng.randint(*KG_TENTHS_PER_ORDER) for _ in range(orders)),
                "crates": crates,
                "empties": crates if rng.random() < EMPTIES_SHARE else 0,
                "window_start_s": start,
                "window_end_s": end,
            }
        )
    if food_bank:
        for i in rng.sample(range(size), size // 6):
            stops[i]["donation_kg_tenths"] = rng.randint(50, 200)
    capacity = VansIn.model_fields["capacity_crates"].default
    needed = sum(s["crates"] for s in stops) / capacity
    return RouteRequest.model_validate(
        {
            "hub": {"lat": hub[0], "lng": hub[1]},
            "depart_s": DEPART_S,
            "return_by_s": max(s["window_end_s"] for s in stops) + RETURN_MARGIN_S,
            "stops": stops,
            "food_bank": (
                {
                    "lat": FOOD_BANK[0],
                    "lng": FOOD_BANK[1],
                    "window_start_s": FOOD_BANK_WINDOW[0],
                    "window_end_s": FOOD_BANK_WINDOW[1],
                }
                if food_bank
                else None
            ),
            "vans": {"count": min(MAX_VANS, math.ceil(needed * fleet_slack))},
            "time_limit_s": TIME_LIMIT_S,
        }
    )


def instance(req: RouteRequest) -> routing.Instance:
    """The request on the haversine matrix (straight line x `routing.DETOUR`), as the service
    builds it when OSRM does not answer."""
    nodes = routing.nodes_of(req)
    dist, dur = routing.haversine_matrix([(n.lat, n.lng) for n in nodes])
    return routing.Instance(nodes, dist, dur, req)


@dataclass(frozen=True)
class Arm:
    """One arm's plan of a morning, as `routing.check()` replays it."""

    arm: str
    status: str  # "solved" or "fallback" (the plan), "baseline" (nearest neighbour)
    orders: int  # served: on a society the plan visits
    stops: int  # societies served
    vans: int
    km: float
    cost_paise: int
    on_time: float  # share of the served societies reached within their window
    late_min: float
    seconds: float  # wall time
    waves: int  # 2 when the morning has both windows, else 1
    problems: list[str] = field(default_factory=list)

    @property
    def cost_paise_per_order(self) -> float:
        return self.cost_paise / max(self.orders, 1)

    @property
    def km_per_order(self) -> float:
        return self.km / max(self.orders, 1)

    @property
    def seconds_per_wave(self) -> float:
        return self.seconds / max(self.waves, 1)

    @property
    def feasible(self) -> bool:
        return not self.problems


@dataclass(frozen=True)
class RoutingRun:
    seed: int
    size: int
    orders: int
    crates: int
    kg: float
    vans_available: int
    time_limit_s: float
    arms: list[Arm]
    short_limit_s: float | None = None  # the shorter budget of `SHORT_ARM`, when it ran

    def arm(self, name: str) -> Arm:
        return next(a for a in self.arms if a.arm == name)

    def has(self, name: str) -> bool:
        return any(a.arm == name for a in self.arms)


def _waves(inst: routing.Instance) -> int:
    starts = {n.window[0] for n in inst.nodes if n.kind == "drop"}
    return min(2, len(starts))


def _served(inst: routing.Instance, ids: set[str]) -> tuple[int, int]:
    drops = [s for s in inst.req.stops if s.id in ids]
    return len(drops), sum(s.orders for s in drops)


def solve_plan(inst: routing.Instance, time_limit_s: float, name: str = ARMS[0]) -> Arm:
    """Production's `routing.plan()` (both waves, the checker inside) on the haversine matrix."""
    answer = routing.plan(inst, time_limit_s, "haversine")
    ids = {s.id for r in answer.routes for s in r.stops if s.kind == "drop"}
    stops, orders = _served(inst, ids)
    s = answer.summary
    return Arm(
        name,
        answer.status,
        orders,
        stops,
        s.vans,
        s.km,
        s.cost_paise,
        s.on_time,
        s.late_min,
        answer.seconds,
        _waves(inst),
        s.problems,
    )


def solve_nearest(inst: routing.Instance) -> Arm:
    """Production's `routing.nearest_neighbour()`, replayed by `routing.check()`."""
    started = time.perf_counter()
    trips = routing.nearest_neighbour(inst)
    checked = routing.check(inst, trips)
    seconds = time.perf_counter() - started
    ids = {inst.nodes[i].id for t in trips for i in t.stops if inst.nodes[i].kind == "drop"}
    stops, orders = _served(inst, ids)
    return Arm(
        ARMS[1],
        "baseline",
        orders,
        stops,
        checked.vans,
        round(checked.km, 2),
        checked.cost_paise,
        round(checked.on_time, 3),
        round(checked.late_min, 1),
        round(seconds, 3),
        _waves(inst),
        checked.problems,
    )


def run_routing(
    seed: int,
    size: int,
    *,
    time_limit_s: float = TIME_LIMIT_S,
    short_limit_s: float | None = SHORT_LIMIT_S,
) -> RoutingRun:
    """Both arms on one morning; with `short_limit_s`, the plan again at that budget."""
    req = morning(seed, size)
    inst = instance(req)
    arms = [solve_plan(inst, time_limit_s), solve_nearest(inst)]
    if short_limit_s:
        arms.append(solve_plan(inst, short_limit_s, SHORT_ARM))
    return RoutingRun(
        seed,
        size,
        sum(s.orders for s in req.stops),
        sum(s.crates for s in req.stops),
        sum(s.kg_tenths for s in req.stops) / 10,
        req.vans.count,
        time_limit_s,
        arms,
        short_limit_s or None,
    )


METRICS = (
    "on_time",
    "cost_paise_per_order",
    "km_per_order",
    "vans",
    "late_min",
    "seconds",
    "seconds_per_wave",
)


def _value(a: Arm, metric: str) -> float:
    return float(getattr(a, metric))


def saving(run: RoutingRun, arm: str = ARMS[0]) -> float:
    """cost_plan / cost_nn - 1 per served order within a seed (negative: the plan is cheaper)."""
    plan, nn = run.arm(arm), run.arm(ARMS[1])
    return plan.cost_paise_per_order / nn.cost_paise_per_order - 1


def _short(these: list[RoutingRun]) -> bool:
    return all(r.has(SHORT_ARM) for r in these)


def summarise(runs: list[RoutingRun]) -> dict[int, dict[str, Any]]:
    """Size → each arm's metrics (mean ± 95 % t-CI over the seeds) and the paired differences."""
    out: dict[int, dict[str, Any]] = {}
    for size in sorted({r.size for r in runs}):
        these = [r for r in runs if r.size == size]
        names = [*ARMS, SHORT_ARM] if _short(these) else list(ARMS)
        arms = {
            name: {m: estimate([_value(r.arm(name), m) for r in these]) for m in METRICS}
            for name in names
        }
        paired = {
            "cost_vs_nearest": estimate([saving(r) for r in these]),
            "km_vs_nearest": estimate(
                [
                    r.arm(ARMS[0]).km_per_order / max(r.arm(ARMS[1]).km_per_order, 1e-9) - 1
                    for r in these
                ]
            ),
            "on_time_minus_nearest": estimate(
                [r.arm(ARMS[0]).on_time - r.arm(ARMS[1]).on_time for r in these]
            ),
            "vans_minus_nearest": estimate(
                [float(r.arm(ARMS[0]).vans - r.arm(ARMS[1]).vans) for r in these]
            ),
        }
        if SHORT_ARM in arms:
            paired["cost_vs_nearest_short"] = estimate([saving(r, SHORT_ARM) for r in these])
        out[size] = {"arms": arms, "paired": paired}
    return out


def _target(these: list[RoutingRun], arm: str) -> dict[str, Any]:
    """The M-F target's parts for one plan arm over a size's seeds."""
    on_time = estimate([r.arm(arm).on_time for r in these])
    cost = estimate([saving(r, arm) for r in these])
    on_time_lower, cost_upper = on_time.mean - on_time.ci, cost.mean + cost.ci
    solver_plans = all(r.arm(arm).status == "solved" for r in these)
    clean = all(r.arm(arm).feasible for r in these)
    within = (
        on_time_lower >= ON_TIME_TARGET and cost_upper <= -SAVING_TARGET and solver_plans and clean
    )
    return {
        "on_time": on_time.as_dict(),
        "on_time_lower": round(on_time_lower, 4),
        "cost_vs_nearest": cost.as_dict(),
        "cost_vs_nearest_upper": round(cost_upper, 4),
        "solver_plans": solver_plans,
        "no_checker_problems": clean,
        "holds": None if len(these) < MIN_SEEDS else within,
    }


def verdict(runs: list[RoutingRun]) -> dict[int, dict[str, Any]]:
    """Size → the M-F target, the guardrail and the shorter budget (rules in the module
    docstring); no target verdict (None) below `MIN_SEEDS` seeds."""
    out: dict[int, dict[str, Any]] = {}
    for size in sorted({r.size for r in runs}):
        these = [r for r in runs if r.size == size]
        # `routing.waves` gives every wave half the limit, one wave or two.
        wave_limit = max(r.time_limit_s for r in these) / 2
        overhead = max(r.arm(ARMS[0]).seconds_per_wave - r.time_limit_s / 2 for r in these)
        short = None
        if _short(these):
            limit = max(r.short_limit_s or 0.0 for r in these)
            short = {"seconds_a_wave": limit / 2, **_target(these, SHORT_ARM)}
        out[size] = {
            "headline": size == HEADLINE_SIZE,
            "n": len(these),
            "seeds": sorted(r.seed for r in these),
            **_target(these, ARMS[0]),
            "nearest_problems": sum(len(r.arm(ARMS[1]).problems) for r in these),
            "wave_limit_s": wave_limit,
            "guardrail_holds": wave_limit <= WAVE_LIMIT_S,
            "max_seconds_per_wave": round(max(r.arm(ARMS[0]).seconds_per_wave for r in these), 3),
            "max_overhead_s": round(overhead, 3),
            "short_budget": short,
            "rule": "over >= 5 seeds: the plan's on-time 95 % CI lower bound >= 0.95, the per-seed "
            "paired cost_plan / cost_nn - 1 (per served order) 95 % CI upper bound <= -0.10, every "
            "plan the solver's with no checker problem; guardrail: the time limit a wave <= 10 s "
            "(the solver spends it; the wall time over it is the overhead); the shorter budget is "
            "evidence, reference only",
        }
    return out


def machine() -> dict[str, Any]:
    """Where the plans were solved: OR-Tools stops on wall time, so plans depend on it."""
    return {
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
        "python": platform.python_version(),
        "system": platform.platform(),
    }


def settings(
    time_limit_s: float = TIME_LIMIT_S, short_limit_s: float | None = None
) -> dict[str, Any]:
    """What the mornings and the arms came from, for the report's provenance."""
    req = RouteRequest.model_fields
    seed_file = Path(world.__file__)
    return {
        "hub": list(HUB),
        "area": list(AREA),
        "societies": "the seed societies with a hub (boundary centre), then uniform random points "
        "in the area, not snapped to roads",
        "societies_sha256": hashlib.sha256(seed_file.read_bytes()).hexdigest(),
        "windows_s": [list(w) for w in WINDOWS],
        "depart_s": DEPART_S,
        "return_by": "the latest window end + 90 min",
        "orders_per_society": list(ORDERS),
        "kg_tenths_per_order": list(KG_TENTHS_PER_ORDER),
        "bags_per_crate": BAGS_PER_CRATE,
        "empties_share": EMPTIES_SHARE,
        "fleet": f"ceil({FLEET_SLACK} x crates / capacity_crates), at most {MAX_VANS}",
        "food_bank": None,
        "matrix": f"haversine x {routing.DETOUR} at {routing.HAVERSINE_SPEED_MPS} m/s "
        "(routing.haversine_matrix), times the vans' traffic factor",
        "time_limit_s": time_limit_s,
        "short_limit_s": short_limit_s,
        "time_limit_default_ml_s": req["time_limit_s"].default,
        "deterministic": False,
        "platform": machine(),
        "model": routing.MODEL,
        "baseline": "routing.nearest_neighbour",
        "scorer": "routing.check",
        "ortools_version": importlib.metadata.version("ortools"),
        "routing_sha256": hashlib.sha256(Path(routing.__file__).read_bytes()).hexdigest(),
        "headline_size": HEADLINE_SIZE,
    }


def section(runs: list[RoutingRun]) -> dict[str, Any]:
    """The report's `routing` section: settings, per size the arms, the paired differences and
    the verdict. Empty without runs."""
    if not runs:
        return {}
    summary = summarise(runs)
    return {
        "settings": settings(runs[0].time_limit_s, runs[0].short_limit_s),
        "sizes": {
            str(size): {
                "arms": {a: {m: e.as_dict() for m, e in ms.items()} for a, ms in s["arms"].items()},
                "paired": {m: e.as_dict() for m, e in s["paired"].items()},
            }
            for size, s in summary.items()
        },
        "verdict": {str(size): v for size, v in verdict(runs).items()},
    }


def metrics_rows(report: dict[str, Any], details: dict[str, Any]) -> list[dict[str, Any]]:
    """`intel.model_metrics` rows: each arm's metrics per size, and the plan's paired cost
    against nearest neighbour."""
    sec = report.get("routing") or {}
    runs = report.get("runs", {}).get("routing", [])
    rows: list[dict[str, Any]] = []

    def per_seed(size: str, arm: str, metric: str) -> dict[str, float]:
        out = {}
        for r in runs:
            if str(r["size"]) != size:
                continue
            a = next(x for x in r["arms"] if x["arm"] == arm)
            out[str(r["seed"])] = _value(Arm(**a), metric)
        return out

    def row(model: str, metric: str, e: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
        return {
            "model_name": model,
            "version": report["commit"],
            "metric": metric,
            "value": e["mean"],
            "split": "holdout",  # a fixed-seed benchmark set (protocol §3): nothing is trained
            "data_source": "synthetic",
            "details": {**details, "ci95": e["ci95"], **extra},
        }

    for size, s in sec.get("sizes", {}).items():
        for arm, metrics in s["arms"].items():
            for metric, e in metrics.items():
                rows.append(
                    row(
                        f"routing_{arm}",
                        f"{metric}_stops_{size}",
                        e,
                        {"per_seed": per_seed(size, arm, metric)},
                    )
                )
        for metric, e in s["paired"].items():
            rows.append(
                row(
                    f"routing_{ARMS[0]}",
                    f"{metric}_stops_{size}",
                    e,
                    {"verdict": sec["verdict"][size]},
                )
            )
    return rows


def html(report: dict[str, Any]) -> str:
    """The report's M-F section (empty without routing runs)."""
    from gharbasket_training.eval.report import _cell, _table, _verdict

    sec = report.get("routing")
    if not sec:
        return ""
    rupees = lambda e: (  # noqa: E731
        f"{e['mean'] / 100:.2f} ± {e['ci95'] / 100:.2f}" if e["n"] > 1 else f"{e['mean'] / 100:.2f}"
    )
    rows = []
    for size, s in sec["sizes"].items():
        for arm, m in s["arms"].items():
            rows.append(
                [
                    f"{size} societies, {arm}",
                    _cell(m["on_time"]),
                    rupees(m["cost_paise_per_order"]),
                    _cell(m["km_per_order"]),
                    _cell(m["vans"]),
                    _cell(m["late_min"]),
                    _cell(m["seconds_per_wave"]),
                ]
            )
    arms = _table(
        "M-F — van routing on seeded delivery mornings (haversine; both arms production code "
        "scored by routing.check): per seed, then mean ± 95 % t-CI",
        ["morning, arm", "on time", "₹ an order", "km an order", "vans", "late min", "s a wave"],
        rows,
    )

    def shorter(v: dict[str, Any]) -> str:
        s = v["short_budget"]
        if not s:
            return "—"
        return (
            f"{s['seconds_a_wave']:g} s a wave: {s['cost_vs_nearest_upper']:+.3f} / "
            f"{s['on_time_lower']:.3f} ({_verdict(s['holds'])})"
        )

    checks = _table(
        "M-F hypothesis: on time ≥ 95 % (CI lower bound) and ₹/order ≥ 10 % below nearest "
        "neighbour (paired CI upper bound ≤ -0.10); guardrail: the time limit a wave ≤ 10 s",
        [
            "societies",
            "cost vs nearest (paired)",
            "upper",
            "on time, lower",
            "holds",
            "limit a wave (max overhead)",
            "shorter budget: cost upper / on time lower",
        ],
        [
            [
                size + (" (headline)" if v["headline"] else ""),
                _cell(v["cost_vs_nearest"]),
                f"{v['cost_vs_nearest_upper']:+.3f}",
                f"{v['on_time_lower']:.3f}",
                _verdict(v["holds"]),
                f"{v['wave_limit_s']:g} s (+{v['max_overhead_s']:.3f} s; "
                f"{_verdict(v['guardrail_holds'])})",
                shorter(v),
            ]
            for size, v in sec["verdict"].items()
        ],
    )
    st = sec["settings"]
    where = st.get("platform", {})
    note = (
        f'<p class="note">Time limit {st["time_limit_s"]:g} s for both waves (M7), not '
        "deterministic: OR-Tools routing stops on wall time, so plans depend on the machine "
        f"({html_escape(where.get('system', '?'))}, {where.get('cpu_count', '?')} CPUs) and can "
        "change from one run to the next. Distances are haversine x 1.3, as staging (no OSRM); "
        "S-3's numbers were on OSRM; the hub is S-3's. ₹ an order counts the orders each arm "
        "served. No verdict below 5 seeds; sizes other than the headline and the shorter budget "
        "are reference only (eval protocol §7, 2026-11-02).</p>"
    )
    return arms + checks + note


def lines(report: dict[str, Any]) -> list[str]:
    """The CLI's summary lines for the routing section."""
    sec = report.get("routing") or {}
    out = []
    for size, v in sec.get("verdict", {}).items():
        arms = sec["sizes"][size]["arms"]
        plan, nn = arms[ARMS[0]], arms[ARMS[1]]
        holds, cost = v["holds"], v["cost_vs_nearest"]
        said = "no verdict: fewer than 5 seeds" if holds is None else ("holds" if holds else "no")
        rupees = lambda m: m["cost_paise_per_order"]["mean"] / 100  # noqa: E731
        short = v["short_budget"]
        out.append(
            f"routing {size:>3} societies: plan ₹/order {rupees(plan):.2f} on time "
            f"{plan['on_time']['mean']:.3f}; nearest ₹/order {rupees(nn):.2f} on time "
            f"{nn['on_time']['mean']:.3f}; cost vs nearest {cost['mean']:+.3f} ± "
            f"{cost['ci95']:.3f}; {v['wave_limit_s']:g} s a wave (+{v['max_overhead_s']:.3f} s); "
            f"M-F {said}"
            + (
                f"; at {short['seconds_a_wave']:g} s a wave {short['cost_vs_nearest']['mean']:+.3f}"
                f" ± {short['cost_vs_nearest']['ci95']:.3f}, on time {short['on_time']['mean']:.3f}"
                if short
                else ""
            )
        )
    return out


def main(argv: Sequence[str] | None = None) -> int:
    """`python -m gharbasket_training.eval.routing`: the M-F arm alone, JSON and HTML in `--out`
    (the full report comes from `just eval`)."""
    from gharbasket_training.eval import report as rep

    parser = argparse.ArgumentParser(description="M-F routing eval alone (eval protocol §4).")
    parser.add_argument("--seeds", type=int, nargs="+", default=[7, 11, 13, 17, 19])
    parser.add_argument("--sizes", type=int, nargs="+", default=list(SIZES))
    parser.add_argument("--time-limit", type=float, default=TIME_LIMIT_S)
    parser.add_argument(
        "--short-limit", type=float, default=SHORT_LIMIT_S, help="the shorter budget; 0 skips it"
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    check_args(parser, args.sizes, args.time_limit, args.short_limit)
    started = time.perf_counter()
    runs = [
        run_routing(seed, size, time_limit_s=args.time_limit, short_limit_s=args.short_limit)
        for size in sorted(set(args.sizes))
        for seed in args.seeds
    ]
    built = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "commit": rep.commit(),
        "args": {
            "seeds": args.seeds,
            "sizes": sorted(set(args.sizes)),
            "time_limit": args.time_limit,
            "short_limit": args.short_limit,
        },
        "routing": section(runs),
        "runs": {"routing": [asdict(r) for r in runs]},
        "wall_seconds": round(time.perf_counter() - started, 1),
    }
    args.out.mkdir(parents=True, exist_ok=True)
    stem = f"routing-{built['generated_at'][:10]}-{built['commit']}"
    (args.out / f"{stem}.json").write_text(json.dumps(built, indent=2) + "\n")
    page = f'<!doctype html><html lang="en"><meta charset="utf-8"><body>{html(built)}</body></html>'
    (args.out / f"{stem}.html").write_text(page + "\n")
    for line in lines(built):
        print(line)
    print(f"routing: {args.out / stem}.json in {built['wall_seconds']} s")
    return 0


def check_args(
    parser: argparse.ArgumentParser, sizes: list[int], time_limit: float, short_limit: float = 0
) -> None:
    if not all(1 <= s <= MAX_SIZE for s in sizes):
        parser.error(f"routing sizes must be within 1 to {MAX_SIZE}")
    limits = RouteRequest.model_fields["time_limit_s"].metadata
    low = next(m.ge for m in limits if hasattr(m, "ge"))
    high = next(m.le for m in limits if hasattr(m, "le"))
    if not low <= time_limit <= high:
        parser.error(f"the routing time limit must be within {low} to {high} s")
    if short_limit and not low <= short_limit <= high:
        parser.error(f"the shorter routing budget must be 0 or within {low} to {high} s")


if __name__ == "__main__":
    sys.exit(main())
