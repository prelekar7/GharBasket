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
optimism that the fixed v1 does not. Newsvendor cost per unit of demand, as a share of the unit
price: C_o x waste + C_u x stock-out at q_tau (the pinball loss at tau times C_u + C_o, summed over
the points and divided by the demand instead of averaged).
"""

import math
import random
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

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
ARMS = ("v0", "v1", "champion")
METRICS = (
    "wape",
    "mase",
    "pinball_q50",
    "pinball_qtau",
    "coverage_qtau",
    "waste",
    "stockout",
    "newsvendor_cost",
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
        picks = {"v0": v0, "v1": V1, "champion": test.champion}
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
                {arm: _arm(test, m, costs) for arm, m in picks.items() if m in test.wape},
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


def paired(runs: list[ForecastRun]) -> dict[str, Estimate]:
    """v1 - v0 per run (seed), on the runs where both were scored."""
    both = [r for r in runs if "v0" in r.metrics and "v1" in r.metrics]
    return {
        m: estimate([r.metrics["v1"][m] - r.metrics["v0"][m] for r in both])
        for m in METRICS
        if all(m in r.metrics["v0"] and m in r.metrics["v1"] for r in both)
    }
