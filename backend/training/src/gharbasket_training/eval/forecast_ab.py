"""RQ3 (M-E): forecast v0 against v1 on a seeded synthetic uncommitted demand.

The simulator's weeks are boxes (committed demand), so this module derives the uncommitted demand
(add-on lines and tiffins) from what it already produces, for the harness only: the simulator's
own outputs are untouched. Every draw is seeded per seed, household and week.

- **Add-ons:** a product a household ran short of in a week (the simulator's pantry
  `bought_elsewhere`, bought outside its box) becomes an add-on with probability `ADDON_TAKE`,
  times the week's price multiplier to the power `-PRICE_ELASTICITY` (shocks.json) and
  `1 + FESTIVAL_LIFT` per festival day of the week (seed festivals.json), capped at 1. The
  quantity is the shortfall in the product's steps (at least one, at most M4's
  `MAX_ADDON_QTY`). Add-ons are placed as production can place them (ADR-0031, eval protocol §7
  2026-11-02, third entry): as add-on lines on the week's box, so only in a week the household
  ordered and funded (`funded`: the simulator's `--write-db` cut-off draw; a skipped or unfunded
  week loses them), delivered on the box's day (the seed societies deliver on Tuesdays), never
  for a product already on that box (M4 refuses it: the household edits the box line instead)
  or without a price at the hub, and at most `MAX_ADDON_LINES` a box (first by product).
- **Tiffins:** a slot the plan leaves to a no-cook day (meal action `tiffin`) is ordered as a
  tiffin with probability `TIFFIN_TAKE`, one portion per member, product `tiffin-<slot>`.
- **World:** every household the seed samples, one hub (the seed societies'), `WEEKS` weeks from
  `START` with the rules planner and the price shocks. Exogenous inputs: the festival calendar by
  week and the shock multiplier as the price index (1.0 = usual; production reads the 4-week
  index of `intel.price_index`).

The arms come from one production `forecast.backtest` per hub (the same models, windows,
conformal intervals): **v0**, the StatsForecast model or baseline with the lowest WAPE (what the
job would pick without LightGBM); **v1**, LightGBM; **champion**, the job's pick over all models.
v0 and the champion are chosen on the windows they are scored on, so they carry a selection
optimism that the fixed v1 does not. The protocol's two baselines are arms too (eval protocol §4,
§7 2026-11-02): **seasonal_naive** (SeasonalNaive(4)) and **naive** ("buy last week"), each with
the same conformal q_tau as the models, so their newsvendor cost is the baseline's point forecast
with the same buffer rule. Newsvendor cost per unit of demand, as a share of the unit price:
C_o x waste + C_u x stock-out at q_tau (the pinball loss at tau times C_u + C_o, summed over the
points and divided by the demand instead of averaged).

**Per-day split** (`daily`): the job splits a product's weekly q50 and q_tau over the delivery days
by each weekday's share of its units over the 4 complete weeks up to the run (`forecast.
day_shares`). Here every backtest point (a product's target week) is split the same way, with the
shares as of its origin, and scored against the harness's daily orders: `daily_wape` over every
point, `daily_waste` / `daily_stockout` / `daily_newsvendor_cost` on the points with a q_tau, as
the weekly metrics are. `split_wape` splits the *actual* weekly quantity by the same shares: the
error of the split alone, whatever the model.

**M-E target** (`against_target`, §7 2026-11-02), as registered: the champion's WAPE ≤ 0.25 (95 %
CI upper bound) and its paired WAPE gap to seasonal naive ≥ 0.10 (paired CI lower bound); no
verdict below 5 seeds. **RQ3's cost answer** is reported beside it, not part of the target: the
champion's newsvendor cost against each baseline's, paired (below a baseline when the paired CI
upper bound is < 0). Stock-out and waste are reported against their registered ceilings but not
judged (at tau = 0.375 q_tau sits below the median by design). Everything is per seed: a seed with
several hubs counts once, as the mean of its hubs.
"""

import math
import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, cast

import pandas as pd
from gharbasket_sim import config
from gharbasket_sim import pantry as pantry_sim
from gharbasket_sim.catalog import Catalog
from gharbasket_sim.households import sample, societies
from gharbasket_sim.weeks import PLANNERS, cached, simulate

from gharbasket.modules.orders.api import MAX_ADDON_LINES, MAX_ADDON_QTY
from gharbasket.tools.seed import SEED_DELIVERY_WEEKDAY
from gharbasket_training import forecast
from gharbasket_training.eval.stats import Estimate, estimate
from gharbasket_training.jobs.forecast import prices_frame, weeks_frame

ADDON_TAKE = 0.5  # share of shortfalls ordered as an add-on rather than at the corner shop
PRICE_ELASTICITY = 1.0
FESTIVAL_LIFT = 0.1  # per festival day of the week
TIFFIN_TAKE = 0.7  # share of no-cook slots ordered as a tiffin
UNFUNDED = 0.05  # the simulator's --write-db: a week's wallet left short at the cut-off
WEEKS = 20  # room for the 8 origins after the 7 weeks a series needs and the 2-week horizon
START = date(2026, 9, 7)  # the first week of the seed festival calendar
V1 = "LightGBM"
BASELINES = {"seasonal_naive": "SeasonalNaive", "naive": "Naive"}  # arm → model (protocol §4)
ARMS = ("v0", "v1", "champion", *BASELINES)
METRICS = (
    "wape",
    "mase",
    "pinball_q50",
    "pinball_qtau",
    "coverage_qtau",
    "coverage_qtau_weighted",
    "waste",
    "stockout",
    "newsvendor_cost",
    "daily_wape",
    "daily_waste",
    "daily_stockout",
    "daily_newsvendor_cost",
)
MIN_SEEDS = 5  # the protocol's five; fewer → no verdict
WAPE_TARGET = 0.25  # protocol §4: WAPE ≤ 25 % (the champion's 95 % CI upper bound)
CEILINGS = {"stockout": 0.03, "waste": 0.05}  # protocol §4; reported, not judged (§7 2026-11-02)
TAU_NOTE = (
    "Not judged (eval protocol §7, 2026-11-02): at the registered costs (C_u 0.3, C_o 0.5) tau = "
    "0.375, so q_tau sits below the median by design and about 1 - tau of the points stock out; "
    "the registered stock-out ≤ 3 % contradicts those costs. The costs are kept; the M-E target "
    "is judged on WAPE and the gap to seasonal naive, and the newsvendor cost against both "
    "baselines answers RQ3 beside it."
)


@dataclass(frozen=True)
class ForecastRun:
    seed: int
    hub_id: str
    households: int
    series: int
    windows: int
    eval_points: int
    v0_model: str
    champion: str
    wape: dict[str, float]  # every scored model's WAPE
    metrics: dict[str, dict[str, float]]  # arm → metric; an arm is absent when it sat out
    split_wape: float | None = None  # the actual weekly quantity split by the shares: WAPE


def assumptions() -> dict[str, Any]:
    return {
        "addon_take": ADDON_TAKE,
        "price_elasticity": PRICE_ELASTICITY,
        "festival_lift_per_day": FESTIVAL_LIFT,
        "tiffin_take": TIFFIN_TAKE,
        # ADR-0031: add-ons ride on the week's ordered and funded box, on its day.
        "addons_on_box": True,
        "unfunded": UNFUNDED,
        "planner": "rules",
        "costs": {"underage": forecast.Costs().underage, "overage": forecast.Costs().overage},
    }


def _stream(seed: int, household: str, week: int) -> random.Random:
    return random.Random(f"gharbasket-eval-forecast:{seed}:{household}:{week}")  # noqa: S311 (seeded)


def funded(seed: int, household: str, week: int) -> bool:
    """Whether the household's wallet covers the week's box at the cut-off: the first draw of the
    simulator's per-week `--write-db` stream (run.py, through_modules.replay_week)."""
    rng = random.Random(f"gharbasket-sim:{seed}:{household}:{week}:db")  # noqa: S311 (seeded)
    return rng.random() >= UNFUNDED


def take(multiplier: float, festival_days: float) -> float:
    """The chance that a shortfall becomes an add-on in a week."""
    lift = 1 + FESTIVAL_LIFT * festival_days
    return min(1.0, ADDON_TAKE * multiplier**-PRICE_ELASTICITY * lift)


def demand(
    seed: int,
    catalog: Catalog,
    *,
    weeks: int,
    start: date,
    adoption: float | None,
    shocks: config.Shocks | None,
) -> tuple[pd.DataFrame, forecast.Exog, int]:
    """The add-on and tiffin lines (`forecast.LINE_COLUMNS`), the exogenous inputs and the
    number of households."""
    households = sample(seed, config.load(), catalog, adoption)
    hub_of = {str(s.id): str(s.hub_id) for s in societies()}
    plan = cached(PLANNERS["rules"])
    festivals = weeks_frame([(f.day, f.code, dict(f.rules)) for f in catalog.festivals])
    festival_days = {
        pd.Timestamp(ds).date(): float(n)
        for ds, n in zip(festivals["ds"], festivals["festival_days"], strict=True)
    }
    factors = [pantry_sim.weekly_prices(catalog, shocks, w) for w in range(weeks)]
    lines: list[dict[str, Any]] = []
    for h in households:
        rows = simulate(seed, h, catalog, start, weeks, plan, shocks)
        hub = hub_of[h.society_id]
        ordered = {b["week"] for b in rows.boxes if b["ordered"]}
        on_box: dict[int, set[str]] = {}
        for line in rows.box_lines:
            on_box.setdefault(line["week"], set()).add(line["product"])
        placed: dict[int, int] = {}  # add-on lines on each week's box
        rngs: dict[int, random.Random] = {}  # one stream per week, drawn in the rows' order
        rng = lambda w: rngs.setdefault(w, _stream(seed, h.key, w))  # noqa: B023, E731
        for p in rows.pantry:
            w, slug, short = p["week"], p["product"], p["bought_elsewhere"]
            monday = start + timedelta(weeks=w)
            chance = take(factors[w][slug], festival_days.get(monday, 0.0))
            if short <= 0 or rng(w).random() >= chance:
                continue
            product = catalog.products[slug]
            if (
                w not in ordered
                or not funded(seed, h.key, w)
                or slug in on_box.get(w, set())
                or pantry_sim.price_of(product, factors[w]) is None
                or placed.get(w, 0) >= MAX_ADDON_LINES
            ):
                continue  # production has no box to put it on, or M4 refuses it
            placed[w] = placed.get(w, 0) + 1
            step = product.step or 1
            lines.append(
                {
                    "hub_id": hub,
                    # An add-on line rides on the week's box: the box's order and delivery day.
                    "product_id": slug,
                    "order_id": f"{h.key}-{w}-box",
                    "delivery_date": monday + timedelta(days=SEED_DELIVERY_WEEKDAY),
                    "order_kind": "addon",
                    "qty": float(min(MAX_ADDON_QTY, max(1, math.ceil(short / step)))),
                }
            )
        for m in rows.meals:
            if m["action"] == "tiffin" and rng(m["week"]).random() < TIFFIN_TAKE:
                lines.append(
                    {
                        "hub_id": hub,
                        "product_id": f"tiffin-{m['slot']}",
                        "order_id": f"{h.key}-{m['date']}-tiffin",
                        "delivery_date": m["date"],
                        "order_kind": "tiffin",
                        "qty": float(len(h.body["members"])),
                    }
                )
    shocked = sorted({e.product for e in shocks.episodes} if shocks else set())
    prices = prices_frame(
        [
            (slug, start + timedelta(weeks=w), factors[w][slug])
            for w in range(weeks)
            for slug in shocked
        ]
    )
    exog = forecast.Exog(weeks=festivals, prices=prices)
    return pd.DataFrame(lines, columns=forecast.LINE_COLUMNS), exog, len(households)


def _arm(test: forecast.Backtest, model: str, costs: forecast.Costs) -> dict[str, float]:
    measured = {
        "wape": test.wape,
        "mase": test.mase,
        "pinball_q50": test.pinball_q50,
        "pinball_qtau": test.pinball_qtau,
        "coverage_qtau": test.coverage,
        "coverage_qtau_weighted": test.coverage_weighted,
        "waste": test.waste,
        "stockout": test.stockout,
    }
    out = {k: v[model] for k, v in measured.items() if model in v}
    if "waste" in out and "stockout" in out:
        cost = costs.overage * out["waste"] + costs.underage * out["stockout"]
        out["newsvendor_cost"] = round(cost, 4)
    return out


@dataclass(frozen=True)
class Split:
    """A backtest's points split over the days of their target weeks (`daily`)."""

    actual: pd.DataFrame  # 7 rows a point (Monday first): point (its row in `points`), y, share
    points: pd.DataFrame  # the backtest's points (`forecast.Backtest.points`)


def split(lines: pd.DataFrame, test: forecast.Backtest) -> Split | None:
    """Every scored point's target week as 7 days: the daily actual (the harness's add-on and
    tiffin lines) and the weekday share the job would split by, as of the point's origin
    (`forecast.day_shares` over the 4 complete weeks up to it: no later order)."""
    pts = test.points
    if pts is None or pts.empty:
        return None
    mine = cast(pd.DataFrame, lines[lines["hub_id"] == test.hub_id])
    sold: dict[tuple[str, date], float] = {}
    for product, day, qty in zip(
        mine["product_id"], mine["delivery_date"], mine["qty"], strict=True
    ):
        key = (str(product), day)
        sold[key] = sold.get(key, 0.0) + float(qty)
    shares_at: dict[date, tuple[dict[str, dict[int, float]], dict[int, float]]] = {}
    rows = []
    for point, (uid, ds, origin) in enumerate(
        zip(pts["unique_id"], pts["ds"], pts["origin"], strict=True)
    ):
        at = cast(pd.Timestamp, pd.Timestamp(origin)).date()
        if at not in shares_at:
            shares_at[at] = forecast.day_shares(mine, test.hub_id, at)
        product_share, hub_share = shares_at[at]
        shares = product_share.get(str(uid)) or hub_share
        monday = cast(pd.Timestamp, pd.Timestamp(ds)).date()
        for weekday in range(7):
            day = monday + timedelta(days=weekday)
            rows.append(
                {
                    "point": point,
                    "y": sold.get((str(uid), day), 0.0),
                    "share": shares.get(weekday, 0.0),
                }
            )
    return Split(pd.DataFrame(rows), pts)


def daily(cut: Split, model: str, costs: forecast.Costs) -> dict[str, float]:
    """`model`'s weekly q50 and q_tau split over the days (production's v0 approximation, M10
    §3.6), scored against the daily actuals: WAPE on every point, waste, stock-out and the
    newsvendor cost on the points with a q_tau (as the weekly metrics)."""
    if model not in cut.points.columns:
        return {}
    point = cut.actual["point"].to_numpy()
    share = cut.actual["share"].to_numpy(dtype=float)
    y = cut.actual["y"].to_numpy(dtype=float)
    q50 = cut.points[model].to_numpy(dtype=float)[point] * share
    qtau = cut.points[f"{model}_qtau"].to_numpy(dtype=float)[point] * share
    out = {"daily_wape": round(forecast.wape(y, q50), 4)}
    evaluated = ~pd.isna(qtau)
    if evaluated.any():
        y_e, q_e = y[evaluated], qtau[evaluated]
        demand = float(y_e.sum()) or 1.0
        waste = round(float((q_e - y_e).clip(min=0).sum()) / demand, 4)
        stockout = round(float((y_e - q_e).clip(min=0).sum()) / demand, 4)
        out |= {
            "daily_waste": waste,
            "daily_stockout": stockout,
            "daily_newsvendor_cost": round(costs.overage * waste + costs.underage * stockout, 4),
        }
    return out


def split_wape(cut: Split) -> float:
    """The split's own error: the actual weekly quantity split by the shares, against the days."""
    point = cut.actual["point"].to_numpy()
    weekly = cut.points["y"].to_numpy(dtype=float)[point]
    share = cut.actual["share"].to_numpy(dtype=float)
    return round(forecast.wape(cut.actual["y"].to_numpy(dtype=float), weekly * share), 4)


def run_forecast(
    seed: int,
    catalog: Catalog,
    *,
    weeks: int = WEEKS,
    start: date = START,
    adoption: float | None = None,
    shocks: config.Shocks | None = None,
) -> list[ForecastRun]:
    """One run per hub: the production backtest on the seed's synthetic uncommitted demand."""
    costs = forecast.Costs()
    lines, exog, households = demand(
        seed, catalog, weeks=weeks, start=start, adoption=adoption, shocks=shocks
    )
    last_week = start + timedelta(weeks=weeks - 1)
    series = forecast.weekly_series(lines, last_week)
    runs = []
    for hub in sorted({str(h) for h in series["hub_id"]}):
        test = forecast.backtest(series, hub, costs.tau, exog)
        v0 = min(
            (m for m in forecast.MODELS if m != V1 and m in test.wape),
            key=lambda m: test.wape[m],
            default="Naive",
        )
        picks = {"v0": v0, "v1": V1, "champion": test.champion} | BASELINES
        cut = split(lines, test)
        runs.append(
            ForecastRun(
                seed,
                hub,
                households,
                test.series,
                test.windows,
                test.eval_points,
                v0,
                test.champion,
                dict(test.wape),
                {
                    arm: _arm(test, m, costs) | (daily(cut, m, costs) if cut else {})
                    for arm, m in picks.items()
                    if m in test.wape
                },
                split_wape(cut) if cut else None,
            )
        )
    return runs


def per_seed(
    runs: list[ForecastRun], value: Callable[[ForecastRun], float | None]
) -> dict[int, float]:
    """Seed → `value` of its runs, the mean over the seed's hubs (runs where it is None are left
    out). Every estimate and every per-seed row is built from this, so a seed counts once."""
    values: dict[int, list[float]] = {}
    for r in runs:
        v = value(r)
        if v is not None:
            values.setdefault(r.seed, []).append(v)
    return {seed: round(sum(vs) / len(vs), 4) for seed, vs in sorted(values.items())}


def metric_of(arm: str, metric: str) -> Callable[[ForecastRun], float | None]:
    return lambda r: r.metrics.get(arm, {}).get(metric)


def difference(arm: str, base: str, metric: str) -> Callable[[ForecastRun], float | None]:
    """`arm` - `base` in one run, when both scored the metric."""

    def value(r: ForecastRun) -> float | None:
        a, b = r.metrics.get(arm, {}).get(metric), r.metrics.get(base, {}).get(metric)
        return None if a is None or b is None else a - b

    return value


def _over_seeds(runs: list[ForecastRun], value: Callable[[ForecastRun], float | None]) -> Estimate:
    return estimate(list(per_seed(runs, value).values()))


def summarise(runs: list[ForecastRun]) -> dict[str, dict[str, Estimate]]:
    """Arm → metric → mean ± 95 % t-CI over the seeds where the arm was scored."""
    return {arm: {m: _over_seeds(runs, metric_of(arm, m)) for m in METRICS} for arm in ARMS}


def paired(runs: list[ForecastRun], arm: str = "v1", base: str = "v0") -> dict[str, Estimate]:
    """`arm` - `base` per seed, on the seeds where both were scored."""
    return {m: _over_seeds(runs, difference(arm, base, m)) for m in METRICS}


def against_baselines(runs: list[ForecastRun]) -> dict[str, dict[str, Estimate]]:
    """Baseline arm → metric → the champion minus the baseline, paired by seed."""
    return {base: paired(runs, "champion", base) for base in BASELINES}


def _upper(e: Estimate) -> float:
    return e.mean + e.ci


def _lower(e: Estimate) -> float:
    return e.mean - e.ci


def against_target(runs: list[ForecastRun]) -> dict[str, Any]:
    """The M-E target as registered (eval protocol §4; how it is computed: §7, 2026-11-02), on
    the seeds where the champion and both baselines were scored: the champion's WAPE ≤ 0.25
    (95 % CI upper bound) and its paired WAPE gap to seasonal naive (seasonal naive - champion)
    ≥ 0.10 (paired CI lower bound). `target_holds` is None (no verdict) below `MIN_SEEDS` seeds.
    Beside it and not part of it, RQ3's cost answer: the champion's newsvendor cost minus each
    baseline's, paired (`below` when the CI upper bound is < 0). Stock-out and waste are listed
    against their registered ceilings, not judged (`TAU_NOTE`)."""
    scored = [r for r in runs if "champion" in r.metrics and all(b in r.metrics for b in BASELINES)]
    seeds = sorted({r.seed for r in scored})
    enough = len(seeds) >= MIN_SEEDS
    wape = _over_seeds(scored, metric_of("champion", "wape"))
    gap = _over_seeds(scored, difference("seasonal_naive", "champion", "wape"))
    tol = 1e-9  # the metrics are rounded to 4 decimals
    checks: dict[str, dict[str, Any]] = {
        "wape_le_0_25": {
            "value": wape.as_dict(),
            "bound": round(_upper(wape), 4),
            "rule": "champion WAPE, 95 % CI upper bound ≤ 0.25",
            "holds": _upper(wape) <= WAPE_TARGET + tol if enough else None,
        },
        "gap_to_seasonal_naive_ge_0_10": {
            "value": gap.as_dict(),
            "bound": round(_lower(gap), 4),
            "rule": "seasonal naive WAPE - champion WAPE, paired 95 % CI lower bound ≥ 0.10",
            "holds": _lower(gap) >= forecast.PROTOCOL_GAP - tol if enough else None,
        },
    }
    cost: dict[str, dict[str, Any]] = {}
    for base in BASELINES:
        diff = _over_seeds(scored, difference("champion", base, "newsvendor_cost"))
        cost[base] = {
            "value": diff.as_dict(),
            "bound": round(_upper(diff), 4),
            "rule": f"champion - {base} newsvendor cost, paired 95 % CI upper bound < 0",
            "below": _upper(diff) < 0 if enough and diff.n == len(seeds) else None,
        }
    judged = [c["holds"] for c in checks.values()]
    below = [c["below"] for c in cost.values()]
    champion = lambda m: _over_seeds(scored, metric_of("champion", m)).as_dict()  # noqa: E731
    return {
        "seeds": seeds,
        "n": len(seeds),
        "checks": checks,
        "target_holds": None if None in judged else all(judged),
        # RQ3: do the newsvendor buffers cost less than the baselines'? Reported, not the target.
        "newsvendor_cost": cost,
        "cost_below_both": None if None in below else all(below),
        "reported": {m: {"value": champion(m), "ceiling": c} for m, c in CEILINGS.items()}
        | {
            # Per point, for reference: the scale-weighted margin need not put it at tau.
            "coverage_qtau": {"value": champion("coverage_qtau"), "tau": _tau()},
            # Each point weighted by its series' scale: what the margin aims at tau.
            "coverage_qtau_weighted": {
                "value": champion("coverage_qtau_weighted"),
                "tau": _tau(),
            },
        },
        "note": TAU_NOTE,
    }


def _tau() -> float:
    return round(forecast.Costs().tau, 4)
