"""RQ3: forecast v0 against v1 on the harness's seeded synthetic uncommitted demand, and its
section of the report."""

import math
from datetime import timedelta

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
    costs = forecast.Costs()
    for metrics in run.metrics.values():
        assert set(metrics) == set(forecast_ab.METRICS)
        expected = costs.overage * metrics["waste"] + costs.underage * metrics["stockout"]
        assert metrics["newsvendor_cost"] == pytest.approx(expected, abs=1e-4)


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


def test_the_report_has_the_forecast_section_and_its_metric_rows() -> None:
    runs = [run_of(7, 0.2, 0.3), run_of(11, 0.4, 0.3)]
    built = report.build([], [], args={"weeks": 1}, forecast_runs=runs)
    section = built["forecast"]
    assert section["assumptions"]["addon_take"] == forecast_ab.ADDON_TAKE
    assert section["arms"]["v1"]["wape"]["mean"] == pytest.approx(0.3)
    assert section["v1_minus_v0"]["wape"]["mean"] == pytest.approx(0.0)
    assert [p["seed"] for p in section["picks"]] == [7, 11] and built["seeds"] == [7, 11]
    page = report.to_html(built)
    assert "RQ3" in page and "v1 (LightGBM)" in page and "newsvendor_cost" in page
    rows = [r for r in report.metrics_rows(built) if r["model_name"].startswith("forecast_eval")]
    assert {r["model_name"] for r in rows} == {f"forecast_eval_{a}" for a in forecast_ab.ARMS}
    assert all(r["split"] == "backtest" and r["data_source"] == "synthetic" for r in rows)
    assert not math.isnan(rows[0]["value"])
    without = report.build([], [], args={"weeks": 1})
    assert without["forecast"] == {} and "RQ3" not in report.to_html(without)
