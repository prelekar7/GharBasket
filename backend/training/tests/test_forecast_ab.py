"""RQ3: forecast v0 against v1 on the harness's seeded synthetic uncommitted demand, and its
section of the report."""

import math
from datetime import timedelta
from typing import cast

import pandas as pd
import pytest
from gharbasket_sim import config
from gharbasket_sim.catalog import Catalog, from_seed_files
from gharbasket_training import forecast
from gharbasket_training.eval import forecast_ab, report
from gharbasket_training.eval.forecast_ab import ForecastRun


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return from_seed_files()


@pytest.fixture(scope="module")
def small(catalog: Catalog) -> list[ForecastRun]:
    """Seed 7, 2 % adoption, 11 weeks: the fewest with three backtest origins."""
    return forecast_ab.run_forecast(
        7, catalog, weeks=11, adoption=0.02, shocks=config.load_shocks()
    )


def test_a_price_shock_lowers_the_add_on_take_and_a_festival_raises_it() -> None:
    usual = forecast_ab.take(1.0, 0)
    assert usual == forecast_ab.ADDON_TAKE
    assert forecast_ab.take(1.8, 0) == pytest.approx(usual / 1.8)
    assert forecast_ab.take(1.0, 2) == pytest.approx(usual * 1.2)
    assert forecast_ab.take(0.1, 7) == 1.0  # a probability


def test_the_demand_is_add_ons_and_tiffins_seeded_and_grounded_in_the_simulator(
    catalog: Catalog,
) -> None:
    shocks = config.load_shocks()
    start = forecast_ab.START
    lines, exog, households = forecast_ab.demand(
        7, catalog, weeks=4, start=start, adoption=0.02, shocks=shocks
    )
    again, _, _ = forecast_ab.demand(7, catalog, weeks=4, start=start, adoption=0.02, shocks=shocks)
    pd.testing.assert_frame_equal(lines, again)  # same seed, same orders
    other, _, _ = forecast_ab.demand(
        11, catalog, weeks=4, start=start, adoption=0.02, shocks=shocks
    )
    assert not lines.equals(other)
    assert households > 0 and list(lines.columns) == forecast.LINE_COLUMNS
    assert set(lines["order_kind"]) == {"addon", "tiffin"}  # no box: only uncommitted demand
    assert (lines["qty"] > 0).all() and (lines["qty"] == lines["qty"].round()).all()  # steps
    tiffins = lines[lines["order_kind"] == "tiffin"]
    assert set(tiffins["product_id"]) <= {"tiffin-breakfast", "tiffin-lunch", "tiffin-dinner"}
    addons = lines[lines["order_kind"] == "addon"]
    assert set(addons["product_id"]) <= set(catalog.products)
    days = pd.to_datetime(lines["delivery_date"])
    assert days.min() >= pd.Timestamp(start)
    assert days.max() < pd.Timestamp(start + timedelta(weeks=4))
    assert lines["hub_id"].nunique() == 1  # the seed societies share one hub
    # What is known in advance: the festival calendar by week, the shocks as the price index.
    assert exog.weeks is not None and exog.prices is not None
    assert (exog.weeks["festival_days"] > 0).all()
    tomato = exog.prices[exog.prices["unique_id"] == "tomato"].set_index("ds")["price_index"]
    assert tomato[pd.Timestamp(start + timedelta(weeks=1))] == 1.8  # shocks.json: week 1-3
    assert tomato[pd.Timestamp(start)] == 1.0


def test_the_backtest_scores_v0_v1_and_the_champion_on_the_same_windows(
    small: list[ForecastRun],
) -> None:
    (run,) = small
    assert run.windows >= forecast.MIN_ORIGINS and run.series > 0 and run.eval_points > 0
    assert run.v0_model != forecast_ab.V1 and run.v0_model in forecast.MODELS
    assert run.wape[run.v0_model] == min(w for m, w in run.wape.items() if m != forecast_ab.V1)
    assert run.champion == min(run.wape, key=lambda m: run.wape[m])
    assert set(run.metrics) == set(forecast_ab.ARMS)
    assert run.metrics["v1"]["wape"] == run.wape["LightGBM"]
    assert run.metrics["champion"] == run.metrics["v0" if run.champion != "LightGBM" else "v1"]
    # The protocol's baselines are arms from the same backtest (same windows, same q_tau rule).
    assert run.metrics["seasonal_naive"]["wape"] == run.wape["SeasonalNaive"]
    assert run.metrics["naive"]["wape"] == run.wape["Naive"]
    for arm, model in forecast_ab.BASELINES.items():
        if run.champion == model:
            assert run.metrics[arm] == run.metrics["champion"]
    assert run.split_wape is not None and run.split_wape >= 0
    costs = forecast.Costs()
    for metrics in run.metrics.values():
        assert set(metrics) == set(forecast_ab.METRICS)
        expected = costs.overage * metrics["waste"] + costs.underage * metrics["stockout"]
        assert metrics["newsvendor_cost"] == pytest.approx(expected, abs=1e-4)
        daily = costs.overage * metrics["daily_waste"] + costs.underage * metrics["daily_stockout"]
        assert metrics["daily_newsvendor_cost"] == pytest.approx(daily, abs=1e-4)
        # Splitting a week over its days can only add error: |Σ_d e_d| ≤ Σ_d |e_d|.
        assert metrics["daily_wape"] >= metrics["wape"] - 1e-4


def test_the_backtest_is_reproducible(catalog: Catalog, small: list[ForecastRun]) -> None:
    again = forecast_ab.run_forecast(
        7, catalog, weeks=11, adoption=0.02, shocks=config.load_shocks()
    )
    assert again == small


def test_too_short_a_history_has_no_windows_and_no_arms(catalog: Catalog) -> None:
    (run,) = forecast_ab.run_forecast(7, catalog, weeks=8, adoption=0.02)
    assert run.windows == 0 and run.metrics == {} and run.v0_model == "Naive"


def run_of(seed: int, v0: float, v1: float | None) -> ForecastRun:
    arm = lambda w: {m: w for m in forecast_ab.METRICS}  # noqa: E731
    metrics = {"v0": arm(v0), "champion": arm(v0)} | ({"v1": arm(v1)} if v1 is not None else {})
    return ForecastRun(seed, "hub", 10, 5, 8, 80, "AutoETS", "AutoETS", {}, metrics)


def test_arms_are_summarised_over_seeds_and_v1_is_paired_with_v0() -> None:
    runs = [run_of(7, 0.2, 0.3), run_of(11, 0.4, 0.3), run_of(13, 0.3, None)]
    summary = forecast_ab.summarise(runs)
    assert summary["v0"]["wape"].n == 3 and summary["v0"]["wape"].mean == pytest.approx(0.3)
    assert summary["v1"]["wape"].n == 2  # v1 sat out seed 13
    diff = forecast_ab.paired(runs)["wape"]
    assert diff.n == 2 and diff.mean == pytest.approx(0.0)
    assert forecast_ab.paired([run_of(13, 0.3, None)])["wape"].n == 0


def judged(
    seed: int,
    champion: float,
    seasonal: float,
    *,
    cost: float = 0.10,
    seasonal_cost: float = 0.20,
    naive_cost: float = 0.20,
    hub: str = "hub",
) -> ForecastRun:
    """A run with only what the M-E target and RQ3's cost read: WAPE and the newsvendor cost."""
    arm = lambda w, c: {"wape": w, "newsvendor_cost": c}  # noqa: E731
    metrics = {
        "champion": arm(champion, cost),
        "seasonal_naive": arm(seasonal, seasonal_cost),
        "naive": arm(0.5, naive_cost),
    }
    return ForecastRun(seed, hub, 10, 5, 8, 80, "AutoETS", "AutoETS", {}, metrics)


SEEDS = (7, 11, 13, 17, 19)


def test_the_champion_is_paired_with_each_baseline_by_seed() -> None:
    runs = [judged(7, 0.2, 0.35), judged(11, 0.2, 0.33), judged(13, 0.3, 0.37)]
    against = forecast_ab.against_baselines(runs)
    assert set(against) == set(forecast_ab.BASELINES)
    wape = against["seasonal_naive"]["wape"]
    assert wape.n == 3 and wape.mean == pytest.approx(-0.1167, abs=1e-4)  # champion - baseline
    assert against["naive"]["newsvendor_cost"].mean == pytest.approx(-0.1)
    # A seed without the baseline arm is not paired.
    lone = ForecastRun(19, "hub", 1, 1, 3, 9, "Naive", "Naive", {}, {"champion": {"wape": 0.2}})
    assert forecast_ab.against_baselines([*runs, lone])["seasonal_naive"]["wape"].n == 3


def test_a_seed_with_two_hubs_counts_once_as_the_mean_of_its_hubs() -> None:
    runs = [judged(7, 0.2, 0.3, hub="a"), judged(7, 0.4, 0.6, hub="b"), judged(11, 0.3, 0.4)]
    assert forecast_ab.per_seed(runs, forecast_ab.metric_of("champion", "wape")) == {
        7: pytest.approx(0.3),
        11: pytest.approx(0.3),
    }
    gap = forecast_ab.per_seed(runs, forecast_ab.difference("seasonal_naive", "champion", "wape"))
    assert gap == {7: pytest.approx(0.15), 11: pytest.approx(0.1)}
    summary = forecast_ab.summarise(runs)["champion"]["wape"]
    assert summary.n == 2 and summary.mean == pytest.approx(0.3)
    paired = forecast_ab.against_baselines(runs)["seasonal_naive"]["wape"]
    assert paired.n == 2 and paired.mean == pytest.approx(-0.125)


def test_the_m_e_target_holds_on_ci_bounds_over_five_seeds() -> None:
    gaps = (0.15, 0.16, 0.14, 0.15, 0.15)
    runs = [judged(s, 0.20, 0.20 + g) for s, g in zip(SEEDS, gaps, strict=True)]
    target = forecast_ab.against_target(runs)
    assert target["n"] == 5 and target["seeds"] == list(SEEDS)
    checks = target["checks"]
    # The target is the registered one: WAPE and the gap; the cost is RQ3's answer beside it.
    assert set(checks) == {"wape_le_0_25", "gap_to_seasonal_naive_ge_0_10"}
    assert checks["wape_le_0_25"]["bound"] == pytest.approx(0.20)  # no spread: CI 0
    assert checks["gap_to_seasonal_naive_ge_0_10"]["value"]["mean"] == pytest.approx(0.15)
    assert all(c["holds"] for c in checks.values()) and target["target_holds"] is True
    assert set(target["newsvendor_cost"]) == set(forecast_ab.BASELINES)
    assert target["cost_below_both"] is True
    # Stock-out and waste are listed against their ceilings, never judged (§7, 2026-11-02).
    assert target["reported"]["stockout"]["ceiling"] == 0.03
    assert target["reported"]["coverage_qtau"]["tau"] == pytest.approx(0.375)
    assert "0.375" in target["note"]


def test_the_m_e_target_judges_the_bounds_not_the_means() -> None:
    # Gap mean 0.12 ≥ 0.10, but its paired CI lower bound is below it: no.
    wide = (0.02, 0.22, 0.12, 0.05, 0.19)
    runs = [judged(s, 0.20, 0.20 + g) for s, g in zip(SEEDS, wide, strict=True)]
    target = forecast_ab.against_target(runs)
    gap = target["checks"]["gap_to_seasonal_naive_ge_0_10"]
    assert gap["value"]["mean"] == pytest.approx(0.12) and gap["bound"] < 0.10
    assert gap["holds"] is False and target["target_holds"] is False
    assert target["checks"]["wape_le_0_25"]["holds"] is True
    # WAPE mean 0.24 ≤ 0.25 but its upper bound is over it.
    champions = (0.20, 0.28, 0.22, 0.26, 0.24)
    runs = [judged(s, w, w + 0.15) for s, w in zip(SEEDS, champions, strict=True)]
    check = forecast_ab.against_target(runs)["checks"]["wape_le_0_25"]
    assert check["value"]["mean"] == pytest.approx(0.24) and check["holds"] is False


def test_the_newsvendor_cost_is_reported_beside_the_target_not_in_it() -> None:
    runs = [
        judged(s, 0.20, 0.35, cost=0.10, naive_cost=0.09 + 0.001 * i) for i, s in enumerate(SEEDS)
    ]
    target = forecast_ab.against_target(runs)
    cost = target["newsvendor_cost"]
    assert cost["seasonal_naive"]["below"] is True
    assert cost["naive"]["below"] is False  # the champion costs more than buy-last-week
    assert target["cost_below_both"] is False
    assert target["target_holds"] is True  # the cost does not decide the registered target


def test_no_m_e_verdict_below_five_seeds() -> None:
    runs = [judged(s, 0.20, 0.35) for s in SEEDS[:4]]
    target = forecast_ab.against_target(runs)
    assert target["n"] == 4 and target["target_holds"] is None
    assert target["cost_below_both"] is None
    assert all(c["holds"] is None for c in target["checks"].values())
    assert all(c["below"] is None for c in target["newsvendor_cost"].values())
    # Two hubs of one seed are still one seed.
    twice = [*runs, judged(7, 0.2, 0.35, hub="other")]
    assert forecast_ab.against_target(twice)["target_holds"] is None
    assert forecast_ab.against_target([])["target_holds"] is None


def test_the_split_spreads_each_week_over_its_days_by_the_shares_as_of_the_origin(
    catalog: Catalog,
) -> None:
    lines, _, _ = forecast_ab.demand(
        7, catalog, weeks=11, start=forecast_ab.START, adoption=0.02, shocks=None
    )
    series = forecast.weekly_series(lines, forecast_ab.START + timedelta(weeks=10))
    (hub,) = set(series["hub_id"])
    test = forecast.backtest(series, hub, forecast.Costs().tau)
    cut = forecast_ab.split(lines, test)
    assert cut is not None and test.points is not None
    assert len(cut.actual) == 7 * len(test.points)
    days = cast(pd.DataFrame, cut.actual.groupby("point")[["y", "share"]].sum())
    # The days add up to the week the backtest scored, and each week's shares to 1.
    week = test.points["y"].to_numpy(dtype=float)
    assert days["y"].to_numpy(dtype=float) == pytest.approx(week)
    assert days["share"].to_numpy(dtype=float) == pytest.approx(1.0)
    # The shares are the job's, as of the point's origin (no later order).
    origin = cast(pd.Timestamp, pd.Timestamp(test.points["origin"].iloc[0])).date()
    products, hub_share = forecast.day_shares(lines, hub, origin)
    shares = products.get(str(test.points["unique_id"].iloc[0])) or hub_share
    expected = [shares.get(d, 0.0) for d in range(7)]
    assert cut.actual[cut.actual["point"] == 0]["share"].tolist() == pytest.approx(expected)
    known = pd.to_datetime(lines["delivery_date"]) <= pd.Timestamp(origin + timedelta(days=6))
    before = cast(pd.DataFrame, lines[known])
    assert forecast.day_shares(before, hub, origin) == (products, hub_share)


def test_the_daily_metrics_split_q50_and_q_tau_by_the_shares() -> None:
    points = pd.DataFrame(
        {
            "unique_id": ["a"],
            "ds": [pd.Timestamp("2026-10-05")],
            "origin": [pd.Timestamp("2026-09-28")],
            "step": [1],
            "y": [10.0],
            "M": [10.0],
            "M_qtau": [8.0],
        }
    )
    days = [6.0, 4.0, 0, 0, 0, 0, 0]  # Monday 6, Tuesday 4
    share = [0.5, 0.0, 0.5, 0, 0, 0, 0]  # the shares put half on Wednesday
    actual = pd.DataFrame({"point": [0] * 7, "y": days, "share": share})
    cut = forecast_ab.Split(actual, points)
    out = forecast_ab.daily(cut, "M", forecast.Costs())
    # q50 by day 5, 0, 5: |6-5| + |4-0| + |0-5| = 10 over 10 units.
    assert out["daily_wape"] == pytest.approx(1.0)
    # q_tau by day 4, 0, 4: waste 4 (Wednesday), stock-out 2 + 4.
    assert out["daily_waste"] == pytest.approx(0.4) and out["daily_stockout"] == pytest.approx(0.6)
    assert out["daily_newsvendor_cost"] == pytest.approx(0.5 * 0.4 + 0.3 * 0.6)
    assert forecast_ab.split_wape(cut) == pytest.approx(1.0)  # 5, 0, 5 again
    assert forecast_ab.daily(cut, "absent", forecast.Costs()) == {}
    # A point without a q_tau (nested calibration had nothing yet) counts for WAPE only.
    unscored = forecast_ab.Split(actual, points.assign(M_qtau=float("nan")))
    assert set(forecast_ab.daily(unscored, "M", forecast.Costs())) == {"daily_wape"}


def test_the_report_has_the_forecast_section_and_its_metric_rows() -> None:
    runs = [run_of(7, 0.2, 0.3), run_of(11, 0.4, 0.3)]
    built = report.build([], [], args={"weeks": 1}, forecast_runs=runs)
    section = built["forecast"]
    assert section["assumptions"]["addon_take"] == forecast_ab.ADDON_TAKE
    assert section["arms"]["v1"]["wape"]["mean"] == pytest.approx(0.3)
    assert section["v1_minus_v0"]["wape"]["mean"] == pytest.approx(0.0)
    assert [p["seed"] for p in section["picks"]] == [7, 11] and built["seeds"] == [7, 11]
    assert section["targets"]["target_holds"] is None  # no seed with the baselines: no verdict
    page = report.to_html(built)
    assert "RQ3" in page and "v1 (LightGBM)" in page and "newsvendor_cost" in page
    assert "M-E" in page and "no verdict: 0 seed(s)" in page  # no baseline arms
    rows = [r for r in report.metrics_rows(built) if r["model_name"].startswith("forecast_eval")]
    arms = {f"forecast_eval_{a}" for a in ("v0", "v1", "champion")}  # run_of has no baselines
    assert {r["model_name"] for r in rows} == arms
    assert all(r["split"] == "backtest" and r["data_source"] == "synthetic" for r in rows)
    assert not math.isnan(rows[0]["value"])
    assert rows[0]["details"]["per_seed"] == {"7": 0.2, "11": 0.4}


def test_the_report_has_the_baseline_arms_the_pairs_and_the_m_e_verdict() -> None:
    def full(seed: int, champion: float) -> ForecastRun:
        arm = lambda w: {m: w for m in forecast_ab.METRICS}  # noqa: E731
        metrics = {
            "v0": arm(champion),
            "v1": arm(champion + 0.01),
            "champion": arm(champion),
            "seasonal_naive": arm(champion + 0.12),
            "naive": arm(champion + 0.15),
        }
        return ForecastRun(seed, "hub", 10, 5, 8, 80, "AutoETS", "AutoETS", {}, metrics, 0.4)

    runs = [full(s, 0.18 + 0.001 * i) for i, s in enumerate(SEEDS)]
    built = report.build([], [], args={"weeks": 1}, forecast_runs=runs)
    section = built["forecast"]
    assert section["arms"]["seasonal_naive"]["wape"]["mean"] == pytest.approx(0.302)
    assert section["champion_minus"]["naive"]["wape"]["mean"] == pytest.approx(-0.15)
    assert section["split_wape"]["mean"] == pytest.approx(0.4)
    assert section["targets"]["target_holds"] is True
    assert section["targets"]["cost_below_both"] is True
    assert section["per_seed"]["champion"]["wape"] == {
        "7": 0.18,
        "11": 0.181,
        "13": 0.182,
        "17": 0.183,
        "19": 0.184,
    }
    page = report.to_html(built)
    assert "seasonal naive" in page and "naive (buy last week)" in page
    assert "champion - seasonal naive (paired)" in page and "): holds. RQ3, newsvendor cost" in page
    names = {r["model_name"] for r in report.metrics_rows(built)}
    assert {f"forecast_eval_{a}" for a in forecast_ab.ARMS} <= names
    assert {f"forecast_eval_champion_minus_{b}" for b in forecast_ab.BASELINES} <= names
    paired_rows = [
        r
        for r in report.metrics_rows(built)
        if r["model_name"] == "forecast_eval_champion_minus_seasonal_naive"
        and r["metric"] == "wape"
    ]
    assert paired_rows[0]["value"] == pytest.approx(-0.12)
    assert paired_rows[0]["details"]["per_seed"]["7"] == pytest.approx(-0.12)
    assert paired_rows[0]["details"]["m_e_target_holds"] is True
    assert paired_rows[0]["details"]["rq3_cost_below_both"] is True
    assert all(
        len(r["model_name"]) <= 60 and len(r["metric"]) <= 40 for r in report.metrics_rows(built)
    )
    without = report.build([], [], args={"weeks": 1})
    assert without["forecast"] == {} and "RQ3" not in report.to_html(without)
