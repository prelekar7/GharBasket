# The pandas stubs are too loose for these frames; the tests hold the shapes to account.
# pyright: reportAttributeAccessIssue=false, reportReturnType=false, reportCallIssue=false
# pyright: reportArgumentType=false, reportGeneralTypeIssues=false
"""Demand forecast v0 with newsvendor procurement quantities (handbook 08 M-E, M10 §3.6).

Committed demand is known exactly at the cut-off (the locked boxes); the forecast is for the
**uncommitted** part — add-ons, tiffins, late approvals — per hub x product x delivery day.
With v0's data (the simulator) the uncommitted series are sparse, so the models are the
intermittent-demand ones next to AutoETS, and the point forecast of the total demand is also
reported for the console's "forecast vs actual" chart.

- Series: weekly buckets (the Monday of each delivery week) per hub x product, from the orders
  that reached `locked` or later: `committed` = box lines, `uncommitted` = add-on and tiffin lines.
- Models (StatsForecast): AutoETS, CrostonOptimized, SeasonalNaive(4); the baselines of the
  protocol are SeasonalNaive and Naive ("buy last week").
- Backtest: rolling origin, horizon 1, as many windows as the history allows (at least  3 kept).
  WAPE per model; the champion per hub is the model with the lowest backtest WAPE.
- Intervals: conformal, from the champion's backtest residuals (pooled per hub): q_tau = point +
  the tau-quantile of the residuals (never below 0).
- Procurement: order_qty = committed + Q_tau(uncommitted); tau = C_u / (C_u + C_o).
"""

import math
import warnings
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

HORIZON = 1  # delivery weeks ahead (the next cut-off plans the next week)
MIN_POINTS = 4  # below this a series gets the naive answer, no backtest
MODELS = ("AutoETS", "CrostonOptimized", "SeasonalNaive")
BASELINES = ("SeasonalNaive", "Naive")


@dataclass(frozen=True)
class Costs:
    """The newsvendor's two costs per unit, as shares of the unit price."""

    underage: float = 0.3  # the margin (and goodwill) lost on a stock-out
    overage: float = 0.5  # the cost not recovered by the surplus cascade on a leftover

    @property
    def tau(self) -> float:
        return self.underage / (self.underage + self.overage)


@dataclass
class Line:
    hub_id: str
    product_id: str
    for_date: date
    committed: float
    uncommitted_q50: float
    uncommitted_qtau: float
    order_qty: float
    model: str


@dataclass
class Backtest:
    hub_id: str
    wape: dict[str, float]  # model → WAPE on the uncommitted series
    champion: str
    windows: int
    series: int


def monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def weekly_series(lines: pd.DataFrame) -> pd.DataFrame:
    """`lines`: order lines of locked-or-later orders with columns hub_id, product_id,
    delivery_date, kind, qty. Returns one row per hub x product x week with `committed` and
    `uncommitted`, every week filled in (zeros where nothing was sold)."""
    if lines.empty:
        return pd.DataFrame(columns=["hub_id", "product_id", "ds", "committed", "uncommitted"])
    df = lines.copy()
    df["ds"] = pd.to_datetime(df["delivery_date"].map(monday_of))
    df["committed"] = np.where(df["kind"] == "box", df["qty"], 0.0)
    df["uncommitted"] = np.where(df["kind"] == "box", 0.0, df["qty"])
    grouped = df.groupby(["hub_id", "product_id", "ds"], as_index=False)[
        ["committed", "uncommitted"]
    ].sum()
    filled = []
    for key, part in grouped.groupby(["hub_id", "product_id"]):
        hub, product = (str(k) for k in key)
        weeks = pd.date_range(part["ds"].min(), part["ds"].max(), freq="W-MON")
        full = (
            part.set_index("ds")[["committed", "uncommitted"]]
            .reindex(weeks, fill_value=0.0)
            .rename_axis("ds")
            .reset_index()
        )
        full["hub_id"], full["product_id"] = hub, product
        filled.append(full)
    return pd.concat(filled, ignore_index=True)[
        ["hub_id", "product_id", "ds", "committed", "uncommitted"]
    ]


def _statsforecast(train: pd.DataFrame, h: int) -> pd.DataFrame:
    from statsforecast import StatsForecast
    from statsforecast.models import AutoETS, CrostonOptimized, Naive, SeasonalNaive

    sf = StatsForecast(
        models=[
            AutoETS(season_length=1),
            CrostonOptimized(),
            SeasonalNaive(season_length=4),
            Naive(),
        ],
        freq="W-MON",
        n_jobs=1,
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return sf.forecast(df=train, h=h)


def _wape(actual: np.ndarray, predicted: np.ndarray) -> float:
    denominator = float(np.abs(actual).sum())
    if denominator == 0:
        return 0.0 if float(np.abs(predicted).sum()) == 0 else 1.0
    return float(np.abs(actual - predicted).sum() / denominator)


def backtest(series: pd.DataFrame, hub_id: str) -> tuple[Backtest, dict[str, np.ndarray]]:
    """Rolling-origin backtest of the uncommitted series of one hub: WAPE per model and the
    champion's residuals (for the conformal interval)."""
    mine = series[series["hub_id"] == hub_id]
    frame = mine.rename(columns={"uncommitted": "y"})[["product_id", "ds", "y"]].rename(
        columns={"product_id": "unique_id"}
    )
    lengths = frame.groupby("unique_id").size()
    usable = lengths[lengths >= MIN_POINTS + 1].index
    frame = frame[frame["unique_id"].isin(usable)]
    if frame.empty:
        return Backtest(hub_id, {}, "Naive", 0, 0), {}
    windows = int(min(3, lengths[usable].min() - MIN_POINTS))
    actuals: list[np.ndarray] = []
    predictions: dict[str, list[np.ndarray]] = {m: [] for m in (*MODELS, "Naive")}
    last = frame["ds"].max()
    for w in range(windows, 0, -1):
        origin = last - pd.Timedelta(weeks=w)
        train = frame[frame["ds"] <= origin]
        test = frame[frame["ds"] == origin + pd.Timedelta(weeks=HORIZON)]
        if train.empty or test.empty:
            continue
        forecast = _statsforecast(train, HORIZON)
        merged = test.merge(forecast, on=["unique_id", "ds"], how="inner")
        if merged.empty:
            continue
        actuals.append(merged["y"].to_numpy(dtype=float))
        for m in predictions:
            predictions[m].append(np.clip(merged[m].to_numpy(dtype=float), 0, None))
    if not actuals:
        return Backtest(hub_id, {}, "Naive", 0, len(usable)), {}
    actual = np.concatenate(actuals)
    wape = {m: round(_wape(actual, np.concatenate(p)), 4) for m, p in predictions.items() if p}
    champion = min(MODELS, key=lambda m: wape.get(m, math.inf))
    residuals = {m: actual - np.concatenate(p) for m, p in predictions.items() if p}
    return Backtest(hub_id, wape, champion, len(actuals), len(usable)), residuals


def forecast_hub(
    series: pd.DataFrame, hub_id: str, costs: Costs | None = None
) -> tuple[list[Line], Backtest]:
    """Next week's lines for one hub: the committed part is the last known week's boxes
    (M6 replaces it with the cut-off's exact count), the uncommitted part is forecast."""
    costs = costs or Costs()
    mine = series[series["hub_id"] == hub_id]
    if mine.empty:
        return [], Backtest(hub_id, {}, "Naive", 0, 0)
    test, residuals = backtest(series, hub_id)
    pooled = residuals.get(test.champion, np.zeros(1))
    margin = float(max(0.0, np.quantile(pooled, costs.tau))) if pooled.size else 0.0
    frame = mine.rename(columns={"product_id": "unique_id", "uncommitted": "y"})[
        ["unique_id", "ds", "y"]
    ]
    lengths = frame.groupby("unique_id").size()
    long_enough = frame[frame["unique_id"].isin(lengths[lengths >= MIN_POINTS].index)]
    point: dict[str, float] = {}
    if not long_enough.empty:
        answer = _statsforecast(long_enough, HORIZON)
        for row in answer.itertuples(index=False):
            point[str(row.unique_id)] = max(0.0, float(getattr(row, test.champion)))
    last_week = mine["ds"].max()
    next_week = (last_week + pd.Timedelta(weeks=HORIZON)).date()
    lines = []
    for product, part in mine.groupby("product_id"):
        latest = part.sort_values("ds").iloc[-1]
        q50 = point.get(str(product), float(latest["uncommitted"]))  # naive for short series
        qtau = q50 + margin
        model = test.champion if str(product) in point else "Naive"
        lines.append(
            Line(
                hub_id,
                str(product),
                next_week,
                round(float(latest["committed"]), 2),
                round(q50, 2),
                round(qtau, 2),
                round(float(latest["committed"]) + qtau, 2),
                model,
            )
        )
    return lines, test


def as_rows(lines: list[Line]) -> list[dict[str, Any]]:
    return [
        {
            "product_id": line.product_id,
            "for_date": line.for_date.isoformat(),
            "committed": line.committed,
            "uncommitted_q50": line.uncommitted_q50,
            "uncommitted_qtau": line.uncommitted_qtau,
            "order_qty": line.order_qty,
            "model": line.model,
        }
        for line in lines
    ]
