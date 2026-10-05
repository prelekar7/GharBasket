# The pandas stubs are too loose for these frames; the tests hold the shapes to account.
# pyright: reportAttributeAccessIssue=false, reportReturnType=false, reportCallIssue=false
# pyright: reportArgumentType=false, reportGeneralTypeIssues=false
"""Forecast v0: weekly series, the rolling-origin backtest against the baselines, the conformal
quantile and the newsvendor order quantity; the job on a simulated world."""

import random
from datetime import date, timedelta

import pandas as pd
import pytest
from gharbasket_training import forecast
from gharbasket_training.jobs import forecast as job
from sqlalchemy.ext.asyncio import AsyncSession


def lines(weeks: int = 12, products: int = 3, seed: int = 7) -> pd.DataFrame:
    """Twelve Tuesdays of one hub: steady boxes, a few add-ons with a weekly rhythm."""
    rng = random.Random(seed)
    rows = []
    first = date(2026, 10, 6)
    for w in range(weeks):
        day = first + timedelta(weeks=w)
        for p in range(products):
            rows.append(("hub-1", f"product-{p}", day, "box", 20 + p * 5 + rng.randint(-2, 2)))
            addons = 3 + (w % 4) + p + rng.randint(-1, 1)  # a 4-week pattern
            rows.append(("hub-1", f"product-{p}", day, "addon", max(0, addons)))
    return pd.DataFrame(rows, columns=["hub_id", "product_id", "delivery_date", "kind", "qty"])


def test_weekly_series_bucket_committed_and_uncommitted_with_no_gaps() -> None:
    frame = lines(weeks=3)
    frame = frame[
        ~((frame["product_id"] == "product-0") & (frame["delivery_date"] == date(2026, 10, 13)))
    ]

    series = forecast.weekly_series(frame)

    p0 = series[series["product_id"] == "product-0"].sort_values("ds")
    assert len(p0) == 3 and p0["ds"].iloc[0] == pd.Timestamp("2026-10-05")  # the Monday
    assert p0["committed"].iloc[1] == 0 and p0["uncommitted"].iloc[1] == 0  # the gap is a zero
    assert (series["committed"] > 0).sum() == 8 and set(series["hub_id"]) == {"hub-1"}
    assert forecast.weekly_series(frame.iloc[0:0]).empty


def test_the_backtest_scores_every_model_and_picks_a_champion() -> None:
    series = forecast.weekly_series(lines())

    test, residuals = forecast.backtest(series, "hub-1")

    assert set(test.wape) == {"AutoETS", "CrostonOptimized", "SeasonalNaive", "LightGBM", "Naive"}
    assert test.windows == 3 and test.series == 3
    assert test.champion in forecast.MODELS
    assert all(0 <= w <= 2 for w in test.wape.values())
    assert test.champion in residuals and residuals[test.champion].shape == (9,)


def test_forecast_lines_carry_the_newsvendor_quantity() -> None:
    series = forecast.weekly_series(lines())
    costs = forecast.Costs(underage=0.3, overage=0.5)
    assert abs(costs.tau - 0.375) < 1e-9

    planned, test = forecast.forecast_hub(series, "hub-1", costs)

    assert len(planned) == 3 and all(line.for_date == date(2026, 12, 28) for line in planned)
    for line in planned:
        assert line.uncommitted_qtau >= line.uncommitted_q50 >= 0
        assert line.order_qty == round(line.committed + line.uncommitted_qtau, 2)
        assert line.model == test.champion
    rows = forecast.as_rows(planned)
    assert rows[0]["for_date"] == "2026-12-28" and set(rows[0]) >= {"order_qty", "model"}


def test_lightgbm_features_join_festivals_and_prices_and_sit_out_short_windows() -> None:
    series = forecast.weekly_series(lines())
    frame = series.rename(columns={"product_id": "unique_id", "uncommitted": "y"})[
        ["unique_id", "ds", "y"]
    ]
    diwali = pd.Timestamp(date(2026, 11, 9))
    exog = forecast.Exog(
        weeks=pd.DataFrame({"ds": [diwali], "festival_days": [3.0], "fast_days": [1.0]}),
        prices=pd.DataFrame({"unique_id": ["product-0"], "ds": [diwali], "price_index": [1.6]}),
    )

    feats = forecast._features(frame, exog)

    assert set(forecast.FEATURES) <= set(feats.columns)
    week = feats[(feats["ds"] == diwali) & (feats["unique_id"] == "product-0")].iloc[0]
    assert (week["festival_days"], week["fast_days"], week["price_index"]) == (3.0, 1.0, 1.6)
    later = feats[(feats["ds"] > diwali) & (feats["unique_id"] == "product-0")]
    assert (later["price_index"] == 1.6).all()  # carried forward until a newer index
    other = feats[feats["unique_id"] == "product-1"]
    assert (other["price_index"] == 1.0).all() and (other["festival_days"].sum() == 3.0)
    assert feats.groupby("unique_id")["lag1"].apply(lambda s: s.isna().sum()).eq(1).all()

    boosted = forecast._lightgbm(frame, 1, exog)
    assert boosted is not None and len(boosted) == 3 and (boosted["LightGBM"] >= 0).all()
    assert boosted["ds"].eq(frame["ds"].max() + pd.Timedelta(weeks=1)).all()
    # Too few rows to fit: the model sits the window out and the backtest drops it.
    short = frame[frame["ds"] <= frame["ds"].min() + pd.Timedelta(weeks=4)]
    assert forecast._lightgbm(short, 1, None) is None
    test, _ = forecast.backtest(forecast.weekly_series(lines(weeks=6)), "hub-1")
    assert "LightGBM" not in test.wape and test.windows == 2 and "AutoETS" in test.wape

    days = [
        (date(2026, 11, 8), "diwali", {"fasting": False}),
        (date(2026, 11, 9), "diwali", {"fasting": False}),
        (date(2026, 11, 12), "ekadashi", {"fasting": True}),
    ]
    weeks = job.weeks_frame(days)
    assert weeks.to_dict("records") == [
        {"ds": pd.Timestamp(date(2026, 11, 2)), "festival_days": 1.0, "fast_days": 0.0},
        {"ds": diwali, "festival_days": 2.0, "fast_days": 1.0},
    ]
    prices = job.prices_frame([("p", date(2026, 11, 10), 1.5), ("p", date(2026, 11, 11), 1.7)])
    assert prices.to_dict("records") == [{"unique_id": "p", "ds": diwali, "price_index": 1.6}]
    assert job.prices_frame([]).empty


def test_short_series_get_the_naive_answer() -> None:
    series = forecast.weekly_series(lines(weeks=2))
    planned, test = forecast.forecast_hub(series, "hub-1")
    assert test.windows == 0 and all(line.model == "Naive" for line in planned)
    assert planned and all(line.uncommitted_q50 == line.uncommitted_qtau for line in planned)
    assert forecast.forecast_hub(series, "hub-9")[0] == []


@pytest.mark.anyio
@pytest.mark.services
async def test_the_job_reads_sold_order_lines(db_session: AsyncSession) -> None:
    from datetime import date as _date

    from gharbasket_sim.run import simulate_world

    from gharbasket.tools.seed import seed_catalog, seed_delivery, seed_households, seed_societies

    await seed_societies(db_session)
    await seed_households(db_session)
    await seed_catalog(db_session)
    await seed_delivery(db_session)
    await simulate_world(
        db_session,
        seed=7,
        weeks=1,
        start=_date(2026, 10, 5),
        planner="rules",
        adoption=0.02,
        write_db=True,
    )

    frame = await job.order_lines(db_session)

    assert not frame.empty and set(frame["kind"]) <= {"box", "addon", "tiffin"}
    series = forecast.weekly_series(frame)
    planned, test = forecast.forecast_hub(series, str(frame["hub_id"].iloc[0]))
    assert planned and test.windows == 0  # one week of history: naive, no backtest yet
