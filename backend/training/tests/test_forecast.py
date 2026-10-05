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

    assert set(test.wape) == {"AutoETS", "CrostonOptimized", "SeasonalNaive", "Naive"}
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
