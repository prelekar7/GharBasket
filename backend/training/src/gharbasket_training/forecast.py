"""Demand forecast v0/v1 with newsvendor procurement quantities (handbook 08 M-E, M10 §3.6).

Committed demand is known exactly at the cut-off (the locked boxes); the forecast is for the
**uncommitted** part — add-on and tiffin orders — per hub x product x delivery day. With v0's
data (the simulator) the uncommitted series are sparse, so the models are the intermittent-demand
ones next to AutoETS and the two baselines.

- Series: weekly buckets (the Monday of each delivery week) per hub x product from the orders
  that have passed `locked` (every status after it, delivered or not); `committed` = the lines of
  box orders, `uncommitted` = the lines of add-on and tiffin orders (by `Order.kind`). Only
  complete weeks count: the run's current week is still filling, so it is left out of training
  and of `committed`. Every series runs to the run's last complete week (zeros where nothing was
  sold); a series with no sales in the last `STALE_WEEKS` weeks is dropped (a stopped product).
- Models (StatsForecast): AutoETS, CrostonOptimized, SeasonalNaive(4) and Naive ("buy last
  week"); the protocol's baselines (SeasonalNaive, Naive) compete for champion like the others.
- v1 adds a global LightGBM per hub (one booster over every series of the hub; the series id is
  not a feature) with a **direct** multi-step strategy: one model per horizon step h in
  {1, 2}. The features of step h are lags 1-3 counted from the origin (y shifted by h, h+1, h+2),
  the 4-week mean up to the origin, the target week of the year on the circle, the festival and
  fast days of the target week (M2's calendar is known in advance) and the product's mandi price
  index. It takes part in a backtest window only when that window has `LGBM_MIN_ROWS` training
  rows for every step; its residuals enter the same per-step conformal calibration.
- Backtest (eval protocol §3): rolling origin, an origin every week, a 2-week horizon, as many
  origins as the history allows (at least `MIN_ORIGINS`, at most `MAX_ORIGINS`). Every model is
  scored on the same windows: those where every model could forecast (a model that would cut
  them below `MIN_ORIGINS` is left out instead, see `_scored`). WAPE per model over both steps of
  those windows; the champion per hub has the lowest WAPE (a tie goes to the simpler model, in
  the order of `MODELS`). The gap to
  SeasonalNaive and the protocol's "10 points better" check are reported, never a gate.
- Intervals: split conformal on residuals scaled per series (divided by the series' mean weekly
  uncommitted quantity up to the origin), per horizon step, with the (n+1) finite-sample
  correction: q_tau = max(0, q50 + scale x Q_tau). The backtest calibrates every evaluation
  window only on residuals whose target week was known at that window's origin, and reports the
  empirical coverage of q_tau, the pinball loss (q50, q_tau) and MASE on those windows.
- Delivery days: the weekly q50 and q_tau of a product are split over the hub's delivery days by
  each weekday's share of the product's units over the last `SHARE_WEEKS` complete weeks (the
  hub's share of orders per weekday when the product sold nothing then). One line per hub x
  product x delivery day for the next 7 days; days in the current week use the 1-week-ahead
  forecast, days in the next week the 2-week-ahead one.
- Procurement: order_qty = committed + Q_tau(uncommitted); tau = C_u / (C_u + C_o). `committed`
  is the box quantity of the same weekday in the last complete week; M6 replaces it with the
  cut-off's exact count.
"""

import math
import warnings
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, cast

import numpy as np
import pandas as pd

HORIZON = 2  # weeks: the protocol's backtest horizon, and this week + next for operations
MIN_POINTS = 7  # weeks before a series is modelled: StatsForecast's AutoETS refuses n <= 6
MIN_ORIGINS = 3  # fewer backtest origins than this → no backtest, the naive answer
MAX_ORIGINS = 8
STALE_WEEKS = 8  # no sale in this many weeks → the product has stopped, no forecast
SHARE_WEEKS = 4  # weeks behind the split of a weekly forecast over delivery days
PROTOCOL_GAP = 0.10  # eval protocol §4: WAPE ≥ 10 points better than seasonal naive
MODELS = ("Naive", "SeasonalNaive", "CrostonOptimized", "AutoETS", "LightGBM")  # tie → simpler
BASELINES = ("SeasonalNaive", "Naive")
LAGS = (1, 2, 3)  # weeks before the origin; step h shifts y by h + lag - 1
LGBM_MIN_ROWS = 8  # fewer training rows than this for a step: LightGBM sits the window out
EXOG_COLUMNS = ("festival_days", "fast_days", "price_index")
FEATURES = (*(f"lag{lag}" for lag in LAGS), "mean4", "week_sin", "week_cos", *EXOG_COLUMNS)
LGBM_PARAMS: dict[str, Any] = {
    "objective": "regression",
    "learning_rate": 0.05,
    "num_leaves": 7,
    "min_data_in_leaf": 3,
    "bagging_fraction": 0.9,
    "bagging_freq": 1,
    "feature_fraction": 0.9,
    "verbosity": -1,
    "seed": 7,
}
LGBM_ROUNDS = 200
LINE_COLUMNS = ["hub_id", "product_id", "order_id", "delivery_date", "order_kind", "qty"]
SERIES_COLUMNS = ["hub_id", "product_id", "ds", "committed", "uncommitted"]


@dataclass(frozen=True)
class Costs:
    """The newsvendor's two costs per unit, as shares of the unit price."""

    underage: float = 0.3  # the margin (and goodwill) lost on a stock-out
    overage: float = 0.5  # the cost not recovered by the surplus cascade on a leftover

    @property
    def tau(self) -> float:
        return self.underage / (self.underage + self.overage)


@dataclass
class Exog:
    """What is known in advance (v1, M10 §3.6): per week `weeks` (ds, festival_days, fast_days)
    and per product x week `prices` (unique_id, ds, price_index: 1.0 = usual). Missing weeks
    count as no festival and the usual price."""

    weeks: pd.DataFrame | None = None
    prices: pd.DataFrame | None = None


@dataclass
class Line:
    hub_id: str
    product_id: str
    for_date: date  # the delivery day
    committed: float
    uncommitted_q50: float
    uncommitted_qtau: float
    order_qty: float
    model: str


@dataclass
class Backtest:
    hub_id: str
    wape: dict[str, float]  # model → WAPE on the uncommitted series, every window, both steps
    champion: str
    windows: int
    series: int
    tau: float = 0.5
    coverage: dict[str, float] = field(default_factory=dict)  # share of actuals ≤ q_tau
    pinball_q50: dict[str, float] = field(default_factory=dict)
    pinball_qtau: dict[str, float] = field(default_factory=dict)
    mase: dict[str, float] = field(default_factory=dict)
    eval_points: int = 0  # the points behind coverage, pinball and MASE
    # model → horizon step → every scaled residual of the backtest (the production calibration)
    calibration: dict[str, dict[int, np.ndarray]] = field(default_factory=dict)

    @property
    def gap_to_seasonal_naive(self) -> float | None:
        """WAPE points the champion is better than SeasonalNaive by (negative: worse)."""
        if "SeasonalNaive" not in self.wape or self.champion not in self.wape:
            return None
        return round(self.wape["SeasonalNaive"] - self.wape[self.champion], 4)

    @property
    def meets_protocol_gap(self) -> bool | None:
        gap = self.gap_to_seasonal_naive
        return None if gap is None else gap >= PROTOCOL_GAP - 1e-9


def monday_of(day: date) -> date:
    return day - timedelta(days=day.weekday())


def last_complete_week(today: date) -> date:
    """The Monday of the last delivery week that has ended before `today`'s week."""
    return monday_of(today) - timedelta(weeks=1)


def _rows(frame: pd.DataFrame, mask: Any) -> pd.DataFrame:
    """`frame[mask]`, typed as the frame it is (the pandas stubs widen it to a Series)."""
    return cast(pd.DataFrame, frame[mask])


def weekly_series(lines: pd.DataFrame, last_week: date) -> pd.DataFrame:
    """`lines`: sold order lines with columns `LINE_COLUMNS`. Returns one row per hub x product x
    week up to `last_week` (the Monday of the run's last complete week) with `committed` and
    `uncommitted`: every week from the product's first sale filled in (zeros where nothing was
    sold), later weeks left out, and products with no sale in the last `STALE_WEEKS` dropped."""
    empty = pd.DataFrame(columns=SERIES_COLUMNS)
    if lines.empty:
        return empty
    df = lines.copy()
    df["ds"] = pd.to_datetime(df["delivery_date"].map(monday_of))
    end = pd.Timestamp(last_week)
    df = _rows(df, df["ds"] <= end)
    if df.empty:
        return empty
    box = df["order_kind"] == "box"
    df["committed"] = np.where(box, df["qty"], 0.0)
    df["uncommitted"] = np.where(box, 0.0, df["qty"])
    grouped = cast(
        pd.DataFrame,
        df.groupby(["hub_id", "product_id", "ds"], as_index=False)[
            ["committed", "uncommitted"]
        ].sum(),
    )
    recent = end - pd.Timedelta(weeks=STALE_WEEKS - 1)
    filled = []
    for key, part in grouped.groupby(["hub_id", "product_id"]):
        hub, product = (str(k) for k in cast(tuple[Any, ...], key))
        weeks = pd.date_range(part["ds"].min(), end, freq="W-MON")
        full = (
            part.set_index("ds")[["committed", "uncommitted"]]
            .reindex(weeks, fill_value=0.0)
            .rename_axis("ds")
            .reset_index()
        )
        sold = full.loc[full["ds"] >= recent, ["committed", "uncommitted"]].to_numpy().sum()
        if sold <= 0:
            continue  # a stopped product
        full["hub_id"], full["product_id"] = hub, product
        filled.append(full)
    if not filled:
        return empty
    return cast(pd.DataFrame, pd.concat(filled, ignore_index=True)[SERIES_COLUMNS])


def _statsforecast(train: pd.DataFrame, h: int) -> pd.DataFrame:
    from statsforecast import StatsForecast
    from statsforecast.models import AutoETS, CrostonOptimized, Naive, SeasonalNaive

    simple = [CrostonOptimized(), SeasonalNaive(season_length=4), Naive()]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            sf = StatsForecast(models=[AutoETS(season_length=1), *simple], freq="W-MON", n_jobs=1)
            return cast(pd.DataFrame, sf.forecast(df=train, h=h))
        except (NotImplementedError, ValueError):  # AutoETS refuses tiny series
            answer = cast(
                pd.DataFrame,
                StatsForecast(models=simple, freq="W-MON", n_jobs=1).forecast(df=train, h=h),
            )
            answer["AutoETS"] = answer["Naive"]
            return answer


def _features(frame: pd.DataFrame, exog: Exog | None, h: int) -> pd.DataFrame:
    """The features of horizon step `h` for every row of `frame` (unique_id, ds, y; each series
    on a gap-free weekly grid) taken as a target week: lags 1-3 counted from the origin (the week
    `h` before the target), the 4-week mean up to the origin, the target week of the year on the
    circle, and the exogenous columns (zeros and 1.0 where unknown)."""
    df = frame.sort_values(["unique_id", "ds"]).reset_index(drop=True).copy()
    by_series = df.groupby("unique_id")["y"]
    for lag in LAGS:
        df[f"lag{lag}"] = by_series.shift(h + lag - 1)
    df["mean4"] = by_series.transform(lambda s: s.shift(h).rolling(4, min_periods=1).mean())
    week = pd.DatetimeIndex(df["ds"]).isocalendar().week.to_numpy(dtype=float)
    df["week_sin"] = np.sin(2 * np.pi * week / 52)
    df["week_cos"] = np.cos(2 * np.pi * week / 52)
    weeks = exog.weeks if exog is not None else None
    if weeks is not None and not weeks.empty:
        df = df.merge(weeks[["ds", "festival_days", "fast_days"]], on="ds", how="left")
    else:
        df["festival_days"], df["fast_days"] = 0.0, 0.0
    prices = exog.prices if exog is not None else None
    if prices is not None and not prices.empty:
        df = df.merge(
            prices[["unique_id", "ds", "price_index"]], on=["unique_id", "ds"], how="left"
        )
        df["price_index"] = df.groupby("unique_id")["price_index"].ffill()
    else:
        df["price_index"] = 1.0
    df[["festival_days", "fast_days"]] = df[["festival_days", "fast_days"]].fillna(0.0)
    df["price_index"] = df["price_index"].fillna(1.0)
    return df


def _lightgbm(train: pd.DataFrame, exog: Exog | None) -> pd.DataFrame | None:
    """The global LightGBM, direct strategy: one booster per horizon step, each fitted on the
    hub's series with that step's features and asked for the week `h` after each series' last
    week. Returns (unique_id, ds, LightGBM) for steps 1..HORIZON, or None when a step has fewer
    than `LGBM_MIN_ROWS` training rows (the model sits the window out)."""
    import lightgbm as lgb

    last: dict[str, pd.Timestamp] = {}
    for uid, ds in zip(train["unique_id"], train["ds"], strict=True):
        week = cast(pd.Timestamp, pd.Timestamp(ds))
        if str(uid) not in last or week > last[str(uid)]:
            last[str(uid)] = week
    future = pd.DataFrame(
        [
            {"unique_id": uid, "ds": week + pd.Timedelta(weeks=h), "y": np.nan}
            for uid, week in last.items()
            for h in range(1, HORIZON + 1)
        ]
    )
    grid = cast(
        pd.DataFrame, pd.concat([train[["unique_id", "ds", "y"]], future], ignore_index=True)
    )
    columns = list(FEATURES)
    answers = []
    for h in range(1, HORIZON + 1):
        feats = _features(grid, exog, h)
        fit = _rows(feats, feats["y"].notna() & feats[f"lag{LAGS[-1]}"].notna())
        if len(fit) < LGBM_MIN_ROWS:
            return None
        booster = lgb.train(
            LGBM_PARAMS,
            lgb.Dataset(fit[columns].to_numpy(dtype=float), fit["y"].to_numpy(dtype=float)),
            num_boost_round=LGBM_ROUNDS,
        )
        wanted = {(uid, week + pd.Timedelta(weeks=h)) for uid, week in last.items()}
        mask = [
            (str(uid), pd.Timestamp(ds)) in wanted
            for uid, ds in zip(feats["unique_id"], feats["ds"], strict=True)
        ]
        ask = _rows(feats, np.array(mask, dtype=bool))
        predicted = np.asarray(booster.predict(ask[columns].to_numpy(dtype=float)), dtype=float)
        answer = pd.DataFrame({"unique_id": ask["unique_id"], "ds": ask["ds"]})
        answer["LightGBM"] = np.clip(predicted, 0, None)
        answers.append(answer)
    return pd.concat(answers, ignore_index=True)


def _wape(actual: np.ndarray, predicted: np.ndarray) -> float:
    denominator = float(np.abs(actual).sum())
    if denominator == 0:
        return 0.0 if float(np.abs(predicted).sum()) == 0 else 1.0
    return float(np.abs(actual - predicted).sum() / denominator)


def _pinball(actual: np.ndarray, quantile: np.ndarray, tau: float) -> float:
    u = actual - quantile
    return float(np.mean(np.maximum(tau * u, (tau - 1) * u)))


def conformal_quantile(scores: np.ndarray, tau: float) -> float:
    """The split-conformal tau-quantile with the finite-sample correction: the ⌈(n+1)·tau⌉-th
    smallest score (clamped to the sample, so a tau above n/(n+1) gives the largest)."""
    n = int(scores.size)
    if n == 0:
        return 0.0
    k = min(max(math.ceil((n + 1) * tau), 1), n)
    return float(np.sort(scores)[k - 1])


def _uncommitted(series: pd.DataFrame, hub_id: str) -> pd.DataFrame:
    mine = _rows(series, series["hub_id"] == hub_id)
    return pd.DataFrame(
        {
            "unique_id": mine["product_id"].astype(str),
            "ds": pd.to_datetime(mine["ds"]),
            "y": mine["uncommitted"].astype(float),
        }
    )


def _long_enough(frame: pd.DataFrame) -> list[str]:
    """The series with at least `MIN_POINTS` weeks in `frame`."""
    sizes: dict[str, int] = {}
    for uid in frame["unique_id"]:
        sizes[str(uid)] = sizes.get(str(uid), 0) + 1
    return [uid for uid, n in sizes.items() if n >= MIN_POINTS]


def _scales(train: pd.DataFrame) -> pd.DataFrame:
    """Per series: the mean weekly quantity (the conformal scale; 1 for an all-zero history) and
    the mean absolute week-on-week change (the MASE scale; NaN when the series never moved)."""
    rows = []
    for uid, part in train.groupby("unique_id"):
        y = part.sort_values("ds")["y"].to_numpy(dtype=float)
        mean = float(y.mean())
        moves = np.abs(np.diff(y))
        mase = float(moves.mean()) if moves.size and moves.mean() > 0 else math.nan
        rows.append({"unique_id": uid, "scale": mean if mean > 0 else 1.0, "mase_scale": mase})
    return pd.DataFrame(rows, columns=["unique_id", "scale", "mase_scale"])


def _scored(present: dict[int, set[str]]) -> tuple[list[str], list[int]]:
    """Which models are scored, on which windows, given the models that forecast in each window.
    Every scored model is scored on the same windows, those where all of them forecast. A model
    that sat out windows (LightGBM short of rows, AutoETS refusing a window) is left out when
    keeping it would leave fewer than `MIN_ORIGINS` such windows (or fewer than there are, when
    the backtest has fewer); the one with the fewest windows goes first, the more complex on a
    tie. The baselines always forecast."""
    models = [m for m in MODELS if any(m in s for s in present.values())]
    target = min(MIN_ORIGINS, len(present))

    def common() -> list[int]:
        return [w for w, s in sorted(present.items()) if set(models) <= s]

    while len(common()) < target:
        droppable = [m for m in reversed(models) if m not in BASELINES]
        if not droppable:
            break
        models.remove(min(droppable, key=lambda m: sum(m in s for s in present.values())))
    return models, common()


def backtest(
    series: pd.DataFrame, hub_id: str, tau: float = 0.5, exog: Exog | None = None
) -> Backtest:
    """Rolling-origin backtest of a hub's uncommitted series (eval protocol §3: an origin every
    week, a 2-week horizon): WAPE per model; coverage, pinball and MASE on nested conformal
    windows; and every model's scaled residuals per step for the production interval. Models
    are scored on the windows where every scored model could forecast (`_scored`)."""
    frame = _uncommitted(series, hub_id)
    none = Backtest(hub_id, {}, "Naive", 0, 0, tau)
    if frame.empty:
        return none
    step = pd.Timedelta(weeks=1)
    count_series = len(set(frame["unique_id"]))
    weeks = frame["ds"].to_numpy(dtype="datetime64[D]")
    first_origin = weeks.min() + np.timedelta64(7 * (MIN_POINTS - 1), "D")
    latest_origin = weeks.max() - np.timedelta64(7 * HORIZON, "D")
    available = int((latest_origin - first_origin) // np.timedelta64(7, "D")) + 1
    if available < MIN_ORIGINS:
        return Backtest(hub_id, {}, "Naive", 0, count_series, tau)
    count = min(MAX_ORIGINS, available)
    origins = [
        pd.Timestamp(latest_origin - np.timedelta64(7 * (count - 1 - i), "D")) for i in range(count)
    ]
    points = []
    for window, origin in enumerate(origins):
        known = _rows(frame, frame["ds"] <= origin)
        eligible = _long_enough(known)
        train = _rows(known, known["unique_id"].isin(eligible))
        ahead = _rows(
            frame,
            frame["unique_id"].isin(eligible)
            & (frame["ds"] > origin)
            & (frame["ds"] <= origin + step * HORIZON),
        )
        if train.empty or ahead.empty:
            continue
        answer = _statsforecast(train, HORIZON)
        boosted = _lightgbm(train, exog)
        if boosted is not None:
            answer = answer.merge(boosted, on=["unique_id", "ds"], how="left")
        merged = ahead.merge(answer, on=["unique_id", "ds"])
        merged = merged.merge(_scales(train), on="unique_id")
        merged["window"], merged["origin"] = window, origin
        merged["step"] = ((merged["ds"] - origin) / step).astype(int)
        points.append(merged)
    if not points:
        return Backtest(hub_id, {}, "Naive", 0, count_series, tau)
    present = {
        int(part["window"].iloc[0]): {
            m for m in MODELS if m in part.columns and bool(part[m].notna().all())
        }
        for part in points
    }
    models, windows = _scored(present)
    pts = pd.concat(points, ignore_index=True)
    pts = _rows(pts, pts["window"].isin(windows)).reset_index(drop=True)
    actual = pts["y"].to_numpy(dtype=float)
    scale = pts["scale"].to_numpy(dtype=float)
    mase_scale = pts["mase_scale"].to_numpy(dtype=float)
    steps = pts["step"].to_numpy(dtype=int)
    target = pts["ds"].to_numpy()
    origin_of = pts["origin"].to_numpy()
    test = Backtest(hub_id, {}, "Naive", len(windows), len(set(pts["unique_id"])), tau)
    for m in models:
        predicted = np.clip(pts[m].to_numpy(dtype=float), 0, None)
        test.wape[m] = round(_wape(actual, predicted), 4)
        scaled = (actual - predicted) / scale
        test.calibration[m] = {s: scaled[steps == s] for s in range(1, HORIZON + 1)}
        # Nested calibration: each point's interval uses only residuals of the same step whose
        # target week was already known at that point's origin.
        qtau = np.full(actual.shape, np.nan)
        for s, origin in {(int(s), o) for s, o in zip(steps, origin_of, strict=True)}:
            seen = (steps == s) & (target <= origin)
            if seen.any():
                margin = conformal_quantile(scaled[seen], tau)
                mask = (steps == s) & (origin_of == origin)
                qtau[mask] = np.maximum(0.0, predicted[mask] + scale[mask] * margin)
        evaluated = ~np.isnan(qtau)
        if not evaluated.any():
            continue
        y, q50, q = actual[evaluated], predicted[evaluated], qtau[evaluated]
        test.eval_points = int(evaluated.sum())
        test.coverage[m] = round(float(np.mean(y <= q)), 4)
        test.pinball_q50[m] = round(_pinball(y, q50, 0.5), 4)
        test.pinball_qtau[m] = round(_pinball(y, q, tau), 4)
        moved = evaluated & ~np.isnan(mase_scale)
        if moved.any():
            test.mase[m] = round(
                float(np.mean(np.abs(actual[moved] - predicted[moved]) / mase_scale[moved])), 4
            )
    test.champion = min(MODELS, key=lambda m: test.wape.get(m, math.inf))
    return test


def day_shares(
    lines: pd.DataFrame, hub_id: str, last_week: date
) -> tuple[dict[str, dict[int, float]], dict[int, float]]:
    """Each weekday's share of a product's units over the last `SHARE_WEEKS` complete weeks, and
    the hub's share of orders per weekday over the same weeks (the fallback)."""
    first = last_week - timedelta(weeks=SHARE_WEEKS - 1)
    end = last_week + timedelta(days=6)
    units: dict[str, dict[int, float]] = {}
    orders: dict[int, set[str]] = {}
    for hub, product, order, day, qty in zip(
        lines["hub_id"],
        lines["product_id"],
        lines["order_id"],
        lines["delivery_date"],
        lines["qty"],
        strict=True,
    ):
        if hub != hub_id or not first <= day <= end:
            continue
        weekday = day.weekday()
        per_day = units.setdefault(str(product), {})
        per_day[weekday] = per_day.get(weekday, 0.0) + float(qty)
        orders.setdefault(weekday, set()).add(str(order))
    products = {
        product: {d: q / sum(per_day.values()) for d, q in per_day.items()}
        for product, per_day in units.items()
        if sum(per_day.values()) > 0
    }
    total = sum(len(ids) for ids in orders.values())
    return products, {d: len(ids) / total for d, ids in orders.items()} if total else {}


def forecast_hub(
    series: pd.DataFrame,
    lines: pd.DataFrame,
    hub_id: str,
    today: date,
    costs: Costs | None = None,
    exog: Exog | None = None,
) -> tuple[list[Line], Backtest]:
    """One line per product and delivery day for the 7 days after `today`: the weekly forecast
    from the last complete week (step 1 for the rest of this week, step 2 for next week) split
    over the hub's delivery days. `series` comes from `weekly_series(lines, last_week)`. A series
    the champion cannot forecast gets "buy last week" and is labelled Naive."""
    costs = costs or Costs()
    last_week = last_complete_week(today)
    mine = _rows(series, series["hub_id"] == hub_id)
    if mine.empty:
        return [], Backtest(hub_id, {}, "Naive", 0, 0, costs.tau)
    test = backtest(series, hub_id, costs.tau, exog)
    frame = _uncommitted(series, hub_id)
    long_enough = _rows(frame, frame["unique_id"].isin(_long_enough(frame)))
    model_of: dict[str, str] = {}
    weekly: dict[tuple[str, int], float] = {}  # (product, step) → q50
    if test.windows and not long_enough.empty:
        answer = _statsforecast(long_enough, HORIZON)
        if test.champion == "LightGBM":
            boosted = _lightgbm(long_enough, exog)
            if boosted is not None:
                answer = answer.merge(boosted, on=["unique_id", "ds"], how="left")
        if test.champion in answer.columns:
            for row in answer.itertuples(index=False):
                value = float(getattr(row, test.champion))
                if math.isnan(value):
                    continue  # the champion has no answer for this series: buy last week
                ahead = int((pd.Timestamp(cast(Any, row).ds) - pd.Timestamp(last_week)).days // 7)
                uid = str(cast(Any, row).unique_id)
                weekly[uid, ahead] = max(0.0, value)
                model_of[uid] = test.champion
    known = _scales(frame)
    scales = {str(u): float(v) for u, v in zip(known["unique_id"], known["scale"], strict=True)}
    product_share, hub_share = day_shares(lines, hub_id, last_week)
    committed_on: dict[tuple[str, date], float] = {}  # the last complete week's boxes
    for hub, product, day, kind, qty in zip(
        lines["hub_id"],
        lines["product_id"],
        lines["delivery_date"],
        lines["order_kind"],
        lines["qty"],
        strict=True,
    ):
        if hub == hub_id and kind == "box" and last_week <= day <= last_week + timedelta(days=6):
            key = (str(product), day)
            committed_on[key] = committed_on.get(key, 0.0) + float(qty)
    latest_week = pd.Timestamp(last_week)
    latest_of = {  # buy last week
        str(u): float(y)
        for u, ds, y in zip(mine["product_id"], mine["ds"], mine["uncommitted"], strict=True)
        if pd.Timestamp(ds) == latest_week
    }
    planned: list[Line] = []
    for uid in sorted(latest_of):
        model = model_of.get(uid, "Naive")
        latest = latest_of[uid]
        shares = product_share.get(uid) or hub_share
        for offset in range(1, 8):
            day = today + timedelta(days=offset)
            share = shares.get(day.weekday(), 0.0)
            if share <= 0:
                continue
            ahead = (monday_of(day) - last_week).days // 7
            q50_week = weekly.get((uid, ahead), latest)
            residuals = test.calibration.get(model, {}).get(ahead, np.zeros(0))
            margin = conformal_quantile(residuals, costs.tau) if residuals.size else 0.0
            qtau_week = max(0.0, q50_week + scales.get(uid, 1.0) * margin)
            same_day = last_week + timedelta(days=day.weekday())
            committed = committed_on.get((uid, same_day), 0.0)
            q50, qtau = q50_week * share, qtau_week * share
            planned.append(
                Line(
                    hub_id,
                    uid,
                    day,
                    round(committed, 2),
                    round(q50, 2),
                    round(qtau, 2),
                    round(committed + qtau, 2),
                    model,
                )
            )
    return planned, test


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
