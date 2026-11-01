"""RQ3 (M-E): forecast v0 against v1 on a seeded synthetic uncommitted demand.

The simulator draws boxes (committed demand) only, so this module derives the uncommitted orders
(add-ons and tiffins) from what it already produces, for the harness only: the simulator's own
outputs are untouched. Every draw is seeded per seed, household and week.

- **Add-ons:** a product a household ran short of in a week (the simulator's pantry
  `bought_elsewhere`, bought outside its box) becomes an add-on with probability `ADDON_TAKE`,
  times the week's price multiplier to the power `-PRICE_ELASTICITY` (shocks.json) and
  `1 + FESTIVAL_LIFT` per festival day of the week (seed festivals.json), capped at 1. The
  quantity is the shortfall in the product's steps (at least one), delivered on a day drawn
  from the week.
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

**M-E verdict** (`against_target`, §7 2026-11-02): the champion's WAPE ≤ 0.25 (95 % CI upper
bound), its paired WAPE gap to seasonal naive ≥ 0.10 (paired CI lower bound), and its newsvendor
cost below both baselines' (paired CI upper bound < 0); stock-out and waste are reported against
their registered ceilings but not judged (at tau = 0.375 q_tau sits below the median by design).
No verdict below 5 seeds.
"""

import math
import random
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, cast

import pandas as pd
from gharbasket_sim import config
from gharbasket_sim import pantry as pantry_sim
from gharbasket_sim.catalog import Catalog
from gharbasket_sim.households import sample, societies
from gharbasket_sim.weeks import PLANNERS, cached, simulate

from gharbasket_training import forecast
from gharbasket_training.eval.stats import Estimate, estimate
from gharbasket_training.jobs.forecast import prices_frame, weeks_frame

ADDON_TAKE = 0.5  # share of shortfalls ordered as an add-on rather than at the corner shop
PRICE_ELASTICITY = 1.0
FESTIVAL_LIFT = 0.1  # per festival day of the week
TIFFIN_TAKE = 0.7  # share of no-cook slots ordered as a tiffin
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
    "the registered stock-out ≤ 3 % contradicts those costs. The costs are kept; M-E is judged "
    "on WAPE, the gap to seasonal naive and the newsvendor cost."
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
        "planner": "rules",
        "costs": {"underage": forecast.Costs().underage, "overage": forecast.Costs().overage},
    }


def _stream(seed: int, household: str, week: int) -> random.Random:
    return random.Random(f"gharbasket-eval-forecast:{seed}:{household}:{week}")  # noqa: S311 (seeded)


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
        rngs: dict[int, random.Random] = {}  # one stream per week, drawn in the rows' order
        rng = lambda w: rngs.setdefault(w, _stream(seed, h.key, w))  # noqa: B023, E731
        for p in rows.pantry:
            w, slug, short = p["week"], p["product"], p["bought_elsewhere"]
            monday = start + timedelta(weeks=w)
            chance = take(factors[w][slug], festival_days.get(monday, 0.0))
            if short <= 0 or rng(w).random() >= chance:
                continue
            step = catalog.products[slug].step or 1
            lines.append(
                {
                    "hub_id": hub,
                    "product_id": slug,
                    "order_id": f"{h.key}-{w}-addon",
                    "delivery_date": monday + timedelta(days=rng(w).randrange(7)),
                    "order_kind": "addon",
                    "qty": float(max(1, math.ceil(short / step))),
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


def summarise(runs: list[ForecastRun]) -> dict[str, dict[str, Estimate]]:
    """Arm → metric → mean ± 95 % t-CI over the runs where the arm was scored."""
    return {
        arm: {
            m: estimate([r.metrics[arm][m] for r in runs if m in r.metrics.get(arm, {})])
            for m in METRICS
        }
        for arm in ARMS
    }


def paired(runs: list[ForecastRun], arm: str = "v1", base: str = "v0") -> dict[str, Estimate]:
    """`arm` - `base` per run (seed), on the runs where both were scored."""
    both = [r for r in runs if arm in r.metrics and base in r.metrics]
    return {
        m: estimate([r.metrics[arm][m] - r.metrics[base][m] for r in both])
        for m in METRICS
        if all(m in r.metrics[arm] and m in r.metrics[base] for r in both)
    }


def against_baselines(runs: list[ForecastRun]) -> dict[str, dict[str, Estimate]]:
    """Baseline arm → metric → the champion minus the baseline, paired by seed."""
    return {base: paired(runs, "champion", base) for base in BASELINES}


def _upper(e: Estimate) -> float:
    return e.mean + e.ci


def _lower(e: Estimate) -> float:
    return e.mean - e.ci


def against_target(runs: list[ForecastRun]) -> dict[str, Any]:
    """The M-E verdict (eval protocol §4 and §7, 2026-11-02) on the seeds where the champion
    and both baselines were scored: the champion's WAPE ≤ 0.25 (95 % CI upper bound); its paired
    WAPE gap to seasonal naive (seasonal naive - champion) ≥ 0.10 (paired CI lower bound); its
    newsvendor cost below each baseline's (paired CI upper bound of champion - baseline < 0).
    Holds when all of them do; None (no verdict) below `MIN_SEEDS` seeds. Stock-out and waste
    are listed against their registered ceilings, not judged (`TAU_NOTE`)."""
    scored = [r for r in runs if "champion" in r.metrics and all(b in r.metrics for b in BASELINES)]
    seeds = sorted({r.seed for r in scored})
    enough = len(seeds) >= MIN_SEEDS
    champion = lambda m: estimate(  # noqa: E731
        [r.metrics["champion"][m] for r in scored if m in r.metrics["champion"]]
    )
    wape = champion("wape")
    gap = estimate(
        [r.metrics["seasonal_naive"]["wape"] - r.metrics["champion"]["wape"] for r in scored]
    )
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
    for base in BASELINES:
        cost = estimate(
            [
                r.metrics["champion"]["newsvendor_cost"] - r.metrics[base]["newsvendor_cost"]
                for r in scored
                if "newsvendor_cost" in r.metrics["champion"]
                and "newsvendor_cost" in r.metrics[base]
            ]
        )
        checks[f"newsvendor_cost_below_{base}"] = {
            "value": cost.as_dict(),
            "bound": round(_upper(cost), 4),
            "rule": f"champion - {base} newsvendor cost, paired 95 % CI upper bound < 0",
            "holds": _upper(cost) < 0 if enough and cost.n == len(scored) else None,
        }
    judged = [c["holds"] for c in checks.values()]
    return {
        "seeds": seeds,
        "n": len(seeds),
        "checks": checks,
        "holds": None if None in judged else all(judged),
        "reported": {
            m: {"value": champion(m).as_dict(), "ceiling": ceiling, "judged": False}
            for m, ceiling in CEILINGS.items()
        }
        | {"coverage_qtau": {"value": champion("coverage_qtau").as_dict(), "tau": _tau()}},
        "note": TAU_NOTE,
    }


def _tau() -> float:
    return round(forecast.Costs().tau, 4)
