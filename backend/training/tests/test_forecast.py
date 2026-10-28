"""Forecast v0/v1: weekly series of complete weeks, the rolling-origin backtest (2-week horizon,
nested conformal calibration) against the baselines, the direct multi-step LightGBM with its
known-in-advance features, the split over delivery days and the newsvendor order quantity; the
push; the job on a simulated world, read back by M6's call."""

import json
import random
from datetime import date, timedelta
from typing import Any, cast

import httpx2
import numpy as np
import pandas as pd
import pytest
from gharbasket_training import forecast
from gharbasket_training.jobs import forecast as job
from gharbasket_training.settings import Settings
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

TODAY = date(2026, 12, 30)  # a Wednesday; the last complete week starts on 21 Dec


def where(frame: pd.DataFrame, mask: Any) -> pd.DataFrame:
    return cast(pd.DataFrame, frame[mask])  # the pandas stubs widen `frame[mask]`


def lines(weeks: int = 12, products: int = 3, seed: int = 7) -> pd.DataFrame:
    """Twelve Tuesdays of one hub: steady boxes, a few add-on orders with a weekly rhythm."""
    rng = random.Random(seed)
    rows = []
    first = date(2026, 10, 6)
    for w in range(weeks):
        day = first + timedelta(weeks=w)
        for p in range(products):
            box = 20 + p * 5 + rng.randint(-2, 2)
            rows.append(("hub-1", f"product-{p}", f"box-{w}", day, "box", box))
            addons = 3 + (w % 4) + p + rng.randint(-1, 1)  # a 4-week pattern
            rows.append(("hub-1", f"product-{p}", f"addon-{w}", day, "addon", max(0, addons)))
    return pd.DataFrame(rows, columns=forecast.LINE_COLUMNS)


def test_weekly_series_buckets_by_order_kind_and_complete_weeks_only() -> None:
    frame = lines(weeks=3)
    frame = where(
        frame,
        ~((frame["product_id"] == "product-0") & (frame["delivery_date"] == date(2026, 10, 13))),
    )
    extra = pd.DataFrame(
        [
            # an add-on line inside a box order is committed: the order locked with the box
            ("hub-1", "product-1", "box-0", date(2026, 10, 6), "box", 4),
            # the run's current week is still filling: left out
            ("hub-1", "product-1", "box-9", date(2026, 10, 27), "box", 99),
        ],
        columns=forecast.LINE_COLUMNS,
    )
    frame = pd.concat([frame, extra], ignore_index=True)

    series = forecast.weekly_series(frame, forecast.last_complete_week(date(2026, 10, 28)))

    p0 = where(series, series["product_id"] == "product-0").sort_values("ds")
    assert len(p0) == 3 and p0["ds"].iloc[0] == pd.Timestamp("2026-10-05")  # the Monday
    assert p0["committed"].iloc[1] == 0 and p0["uncommitted"].iloc[1] == 0  # the gap is a zero
    p1 = where(series, series["product_id"] == "product-1").sort_values("ds")
    assert p1["ds"].max() == pd.Timestamp("2026-10-19") and 99 not in set(p1["committed"])
    assert p1["committed"].iloc[0] == frame.iloc[2]["qty"] + 4
    assert (series["committed"] > 0).sum() == 8 and set(series["hub_id"]) == {"hub-1"}
    assert forecast.weekly_series(frame.iloc[0:0], date(2026, 10, 19)).empty


def test_every_series_runs_to_the_last_complete_week_and_stopped_products_drop() -> None:
    frame = lines(weeks=12)
    # product-1 stops after week 6, product-2 after week 2: no sale in the last 8 weeks
    week = frame["delivery_date"].map(lambda d: (d - date(2026, 10, 6)).days // 7)
    frame = where(
        frame,
        ~((frame["product_id"] == "product-1") & (week > 6))
        & ~((frame["product_id"] == "product-2") & (week > 2)),
    )

    series = forecast.weekly_series(frame, forecast.last_complete_week(TODAY))

    assert set(series["product_id"]) == {"product-0", "product-1"}
    p1 = where(series, series["product_id"] == "product-1").sort_values("ds")
    assert p1["ds"].max() == pd.Timestamp("2026-12-21")  # zeros after it stopped
    assert (p1.tail(5)[["committed", "uncommitted"]].to_numpy() == 0).all()


def test_the_conformal_quantile_has_the_finite_sample_correction() -> None:
    scores = np.arange(1.0, 10.0)  # n = 9
    assert forecast.conformal_quantile(scores, 0.5) == 5  # ⌈10 · 0.5⌉ = 5th
    assert forecast.conformal_quantile(scores, 0.75) == 8  # ⌈7.5⌉ = 8th, not np.quantile's 7
    assert forecast.conformal_quantile(scores, 0.95) == 9  # beyond the sample: the largest
    assert forecast.conformal_quantile(scores, 0.1) == 1
    assert forecast.conformal_quantile(np.zeros(0), 0.5) == 0


def test_the_backtest_uses_weekly_origins_a_two_week_horizon_and_nested_windows() -> None:
    series = forecast.weekly_series(lines(), forecast.last_complete_week(TODAY))

    test = forecast.backtest(series, "hub-1", 0.375)

    assert set(test.wape) == set(forecast.MODELS) and test.champion in forecast.MODELS
    # 12 weeks, 7 to train, 2 ahead: origins on weeks 7..10 of 12 → 4 windows
    assert test.windows == 4 and test.series == 3
    # step 1 is calibrated from window 2 on, step 2 from window 3 (its target known) → 3 + 2
    assert test.eval_points == 3 * (3 + 2)
    for m in forecast.MODELS:
        assert 0 <= test.wape[m] <= 2 and 0 <= test.coverage[m] <= 1
        assert test.pinball_q50[m] >= 0 and test.pinball_qtau[m] >= 0 and test.mase[m] > 0
        assert set(test.calibration[m]) == {1, 2}
    assert test.gap_to_seasonal_naive == round(
        test.wape["SeasonalNaive"] - test.wape[test.champion], 4
    )
    gap = test.gap_to_seasonal_naive
    assert gap is not None and test.meets_protocol_gap is (gap >= forecast.PROTOCOL_GAP)


def test_origins_are_capped_and_a_baseline_can_be_champion() -> None:
    rows = []
    for w in range(30):  # a step change late on: "buy last week" follows it best
        day = date(2026, 1, 6) + timedelta(weeks=w)
        rows.append(("hub-1", "product-0", f"addon-{w}", day, "addon", 2 if w < 25 else 40))
    frame = pd.DataFrame(rows, columns=forecast.LINE_COLUMNS)
    series = forecast.weekly_series(frame, date(2026, 7, 27))

    test = forecast.backtest(series, "hub-1")

    assert test.windows == forecast.MAX_ORIGINS
    assert test.champion in forecast.BASELINES and test.wape[test.champion] == min(
        test.wape.values()
    )


def test_waste_and_stock_out_at_q_tau_are_the_pinball_loss_split_in_two() -> None:
    """Per point, pinball_tau = tau (y - q)+ + (1 - tau) (q - y)+; summed over the evaluated
    points (the same for every model) and divided by the demand, they are the newsvendor's
    stock-out and waste. So pinball / (tau stock-out + (1 - tau) waste) is the mean demand per
    point, the same for every model."""
    series = forecast.weekly_series(lines(weeks=16), forecast.last_complete_week(TODAY))
    tau = forecast.Costs().tau
    test = forecast.backtest(series, "hub-1", tau)
    assert set(test.waste) == set(test.stockout) == set(test.pinball_qtau) and test.waste
    ratios = []
    for m in test.waste:
        assert test.waste[m] >= 0 and test.stockout[m] >= 0
        split = tau * test.stockout[m] + (1 - tau) * test.waste[m]
        ratios.append(test.pinball_qtau[m] / split)
    assert max(ratios) == pytest.approx(min(ratios), rel=0.02)


def test_forecast_lines_are_per_delivery_day_with_the_newsvendor_quantity() -> None:
    frame = lines()
    series = forecast.weekly_series(frame, forecast.last_complete_week(TODAY))
    costs = forecast.Costs(underage=0.3, overage=0.5)
    assert abs(costs.tau - 0.375) < 1e-9

    planned, test = forecast.forecast_hub(series, frame, "hub-1", TODAY, costs)

    # Tuesdays only; the next 7 days after Wednesday 30 Dec hold one: 5 Jan (next week, step 2)
    assert len(planned) == 3 and all(line.for_date == date(2027, 1, 5) for line in planned)
    last_tuesday = frame[frame["delivery_date"] == date(2026, 12, 22)]
    for line in planned:
        box = last_tuesday[
            (last_tuesday["product_id"] == line.product_id) & (last_tuesday["order_kind"] == "box")
        ]["qty"].sum()
        assert line.committed == box  # the same weekday of the last complete week
        assert line.uncommitted_qtau >= 0 and line.uncommitted_q50 >= 0
        assert line.order_qty == round(line.committed + line.uncommitted_qtau, 2)
        assert line.model == test.champion
    rows = forecast.as_rows(planned)
    assert rows[0]["for_date"] == "2027-01-05" and set(rows[0]) >= {"order_qty", "model"}


def test_lightgbm_is_direct_one_model_per_step_with_features_known_at_the_origin() -> None:
    series = forecast.weekly_series(lines(), forecast.last_complete_week(TODAY))
    frame = forecast._uncommitted(series, "hub-1")
    diwali = pd.Timestamp(date(2026, 11, 9))
    exog = forecast.Exog(
        weeks=pd.DataFrame({"ds": [diwali], "festival_days": [3.0], "fast_days": [1.0]}),
        prices=pd.DataFrame({"unique_id": ["product-0"], "ds": [diwali], "price_index": [1.6]}),
    )

    one, two = forecast._features(frame, exog, 1), forecast._features(frame, exog, 2)

    assert set(forecast.FEATURES) <= set(one.columns)
    p0 = where(frame, frame["unique_id"] == "product-0").sort_values("ds")["y"].to_list()
    row1 = where(one, one["unique_id"] == "product-0").sort_values("ds").iloc[5]
    row2 = where(two, two["unique_id"] == "product-0").sort_values("ds").iloc[5]
    # step h: lag 1 is the origin's week, h weeks before the target
    assert (row1["lag1"], row1["lag2"], row1["lag3"]) == (p0[4], p0[3], p0[2])
    assert (row2["lag1"], row2["lag2"], row2["lag3"]) == (p0[3], p0[2], p0[1])
    assert row2["mean4"] == pytest.approx(sum(p0[0:4]) / 4)
    week = where(one, (one["ds"] == diwali) & (one["unique_id"] == "product-0")).iloc[0]
    assert (week["festival_days"], week["fast_days"]) == (3.0, 1.0)  # the calendar is known
    assert week["price_index"] == 1.0  # Diwali week's index is not known at its origin
    after = where(one, (one["ds"] > diwali) & (one["unique_id"] == "product-0"))
    assert (after["price_index"] == 1.6).all()  # known from the next origin on, carried forward
    week2 = where(
        two, (two["ds"] == diwali + pd.Timedelta(weeks=1)) & (two["unique_id"] == "product-0")
    )
    assert week2["price_index"].iloc[0] == 1.0  # step 2: the origin is two weeks back
    other = where(one, one["unique_id"] == "product-1")
    assert (other["price_index"] == 1.0).all() and other["festival_days"].sum() == 3.0

    boosted = forecast._lightgbm(frame, exog)
    assert boosted is not None and len(boosted) == 3 * forecast.HORIZON
    assert (boosted["LightGBM"] >= 0).all()
    last = frame["ds"].max()
    assert set(boosted["ds"]) == {last + pd.Timedelta(weeks=h) for h in (1, 2)}
    # Too few rows for a step: the model sits the window out.
    short = where(frame, frame["ds"] <= frame["ds"].min() + pd.Timedelta(weeks=4))
    assert forecast._lightgbm(short, None) is None


def test_a_price_row_of_the_target_week_changes_no_feature_and_no_forecast() -> None:
    series = forecast.weekly_series(lines(), forecast.last_complete_week(TODAY))
    frame = forecast._uncommitted(series, "hub-1")
    last = frame["ds"].max()
    known = pd.DataFrame(
        {
            "unique_id": ["product-0", "product-1"],
            "ds": [last - pd.Timedelta(weeks=6), last],
            "price_index": [1.3, 0.8],
        }
    )
    leak = pd.DataFrame(  # the target weeks' own index (and a later one), not known at the origin
        {
            "unique_id": ["product-0", "product-0", "product-1"],
            "ds": [last + pd.Timedelta(weeks=1), last + pd.Timedelta(weeks=2), last],
            "price_index": [5.0, 7.0, 0.8],
        }
    )
    honest = forecast.Exog(prices=known)
    leaky = forecast.Exog(prices=pd.concat([known, leak.iloc[:2]], ignore_index=True))
    # the backtest's test rows: targets after an earlier origin, with their own week's index
    origin = last - pd.Timedelta(weeks=4)
    target_rows = pd.DataFrame(
        {
            "unique_id": ["product-0", "product-0"],
            "ds": [origin + pd.Timedelta(weeks=1), origin + pd.Timedelta(weeks=2)],
            "price_index": [9.0, 9.0],
        }
    )
    in_window = forecast.Exog(prices=pd.concat([known, target_rows], ignore_index=True))

    for h in (1, 2):
        base = forecast._features(frame, honest, h)
        for other in (leaky, in_window):
            feats = forecast._features(frame, other, h)
            test_row = (feats["unique_id"] == "product-0") & (
                feats["ds"] == origin + pd.Timedelta(weeks=h)
            )
            assert (
                feats.loc[test_row, "price_index"].iloc[0]
                == base.loc[test_row, "price_index"].iloc[0]
            )
    # step 1 for the week after `last` reads `last`'s index (0.8), known at the origin
    future = forecast._features(
        pd.concat(
            [
                frame,
                pd.DataFrame(
                    {
                        "unique_id": ["product-1"],
                        "ds": [last + pd.Timedelta(weeks=1)],
                        "y": [np.nan],
                    }
                ),
            ],
            ignore_index=True,
        ),
        honest,
        1,
    )
    assert future.loc[future["ds"] == last + pd.Timedelta(weeks=1), "price_index"].iloc[0] == 0.8

    a, b = forecast._lightgbm(frame, honest), forecast._lightgbm(frame, leaky)
    assert a is not None and b is not None
    pd.testing.assert_frame_equal(a, b)  # deterministic, and blind to the target weeks' prices


def test_autoets_refusing_a_window_sits_it_out_instead_of_copying_naive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import statsforecast.models as models

    class Refusing(models.AutoETS):
        def forecast(self, *args: Any, **kwargs: Any) -> Any:
            raise ValueError("tiny series")

    monkeypatch.setattr(models, "AutoETS", Refusing)
    series = forecast.weekly_series(lines(), forecast.last_complete_week(TODAY))
    train = forecast._uncommitted(series, "hub-1")

    answer = forecast._statsforecast(train, forecast.HORIZON)

    assert "AutoETS" not in answer.columns and "Naive" in answer.columns
    test = forecast.backtest(series, "hub-1")
    assert "AutoETS" not in test.wape and test.windows == 4 and "LightGBM" in test.wape
    planned, test = forecast.forecast_hub(series, lines(), "hub-1", TODAY)
    assert planned and all(line.model != "AutoETS" for line in planned)


def test_lightgbm_sits_out_short_windows_and_models_share_the_scored_windows() -> None:
    present = {0: {"Naive", "SeasonalNaive", "CrostonOptimized", "AutoETS"}}
    present |= {w: set(forecast.MODELS) for w in range(1, 6)}
    models, windows = forecast._scored(present)
    assert models == list(forecast.MODELS) and windows == [1, 2, 3, 4, 5]
    present = {w: {"Naive", "SeasonalNaive", "CrostonOptimized", "AutoETS"} for w in range(4)}
    present[3].add("LightGBM")  # one window only: keeping it would leave 1 < MIN_ORIGINS
    models, windows = forecast._scored(present)
    assert "LightGBM" not in models and windows == [0, 1, 2, 3]

    one_series = where(lines(products=1), lines(products=1)["delivery_date"] >= date(2026, 10, 6))
    series = forecast.weekly_series(one_series, forecast.last_complete_week(TODAY))
    test = forecast.backtest(series, "hub-1")
    assert "LightGBM" not in test.wape and "AutoETS" in test.wape and test.windows == 4


def test_the_job_turns_the_calendar_and_the_price_index_into_weekly_frames() -> None:
    diwali = pd.Timestamp(date(2026, 11, 9))
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


def test_a_low_tau_puts_q_tau_below_the_median() -> None:
    series = forecast.weekly_series(lines(), forecast.last_complete_week(TODAY))

    planned, _ = forecast.forecast_hub(
        series, lines(), "hub-1", TODAY, forecast.Costs(underage=0.1, overage=0.9)
    )

    assert planned and all(line.uncommitted_qtau < line.uncommitted_q50 for line in planned)


def test_weekly_forecasts_are_split_over_the_hubs_delivery_days() -> None:
    rows = []
    for w in range(12):
        tuesday = date(2026, 10, 6) + timedelta(weeks=w)
        friday = tuesday + timedelta(days=3)
        rows += [
            ("hub-1", "milk", f"t{w}", tuesday, "addon", 1),
            ("hub-1", "milk", f"f{w}", friday, "addon", 3),
            ("hub-1", "ghee", f"f{w}", friday, "box", 2),
        ]
        if w < 6:  # sold 6+ weeks ago, nothing in the last 4: falls back to the hub's orders
            rows.append(("hub-1", "jaggery", f"t{w}", tuesday, "addon", 5))
    frame = pd.DataFrame(rows, columns=forecast.LINE_COLUMNS)
    series = forecast.weekly_series(frame, forecast.last_complete_week(TODAY))

    planned, _ = forecast.forecast_hub(series, frame, "hub-1", TODAY)

    by = {(line.product_id, line.for_date): line for line in planned}
    friday, tuesday = date(2027, 1, 1), date(2027, 1, 5)
    assert {d for _, d in by} == {friday, tuesday}  # delivery days only
    assert by["milk", friday].uncommitted_q50 == pytest.approx(
        3 * by["milk", tuesday].uncommitted_q50, abs=0.02
    )  # 3 : 1 by units (the weekly forecast is flat)
    assert ("ghee", tuesday) not in by and by["ghee", friday].committed == 2
    jaggery = {d for p, d in by if p == "jaggery"}
    assert jaggery == {friday, tuesday}  # half the hub's orders on each day


def test_short_series_get_the_naive_answer() -> None:
    frame = lines(weeks=2)
    series = forecast.weekly_series(frame, date(2026, 10, 12))
    planned, test = forecast.forecast_hub(series, frame, "hub-1", date(2026, 10, 21))
    assert test.windows == 0 and all(line.model == "Naive" for line in planned)
    assert planned and all(line.uncommitted_q50 == line.uncommitted_qtau for line in planned)
    assert forecast.forecast_hub(series, frame, "hub-9", date(2026, 10, 21))[0] == []


def test_sold_statuses_are_every_status_past_locked() -> None:
    from gharbasket.modules.orders.api import NOT_SOLD, SOLD
    from gharbasket.modules.orders.models import STATUSES

    assert job.SOLD == SOLD  # one definition, shared with M10's monitor_drift
    assert set(SOLD) | set(NOT_SOLD) == set(STATUSES) and not set(SOLD) & set(NOT_SOLD)
    assert {"locked", "delivered", "undelivered", "redelivery", "refunded"} <= set(SOLD)


@pytest.mark.anyio
async def test_push_sends_the_lines_and_the_metrics_with_the_data_source() -> None:
    frame = lines()
    series = forecast.weekly_series(frame, forecast.last_complete_week(TODAY))
    planned, test = forecast.forecast_hub(series, frame, "hub-1", TODAY)
    sent: dict[str, dict] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.headers["Authorization"] == "Bearer t0ken"
        sent[request.url.path] = json.loads(request.content)
        if request.url.path.endswith("/forecasts"):
            return httpx2.Response(200, json={"accepted": len(planned)})
        return httpx2.Response(200, json={"accepted": 1})

    settings = Settings(api_url="http://api.test/", jobs_token=SecretStr("t0ken"))
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        accepted = await job.push(http, settings, "hub-1", planned, test, "abc123", "pilot")
        with pytest.raises(RuntimeError, match="API_URL"):
            await job.push(http, Settings(), "hub-1", planned, test, "abc123", "pilot")

    assert accepted == len(planned)
    stored = sent["/v1/internal/intel/forecasts"]
    assert stored["data_source"] == "pilot" and stored["hub_id"] == "hub-1"
    assert stored["model"] == f"forecast-{test.champion.lower()}-v1"
    assert len(stored["items"]) == len(planned)
    metrics = sent["/v1/internal/intel/model-metrics"]["items"]
    assert {m["data_source"] for m in metrics} == {"pilot"}
    assert {m["split"] for m in metrics} == {"backtest"}
    names = {(m["model_name"], m["metric"]) for m in metrics}
    for model in forecast.MODELS:
        for metric in ("wape", "coverage_qtau", "pinball_q50", "pinball_qtau", "mase"):
            assert (f"forecast_{model.lower()}", metric) in names
    gap = next(m for m in metrics if m["metric"] == "wape_gap_seasonal_naive")
    assert gap["details"]["meets_protocol_gap"] == test.meets_protocol_gap
    assert gap["details"]["horizon_weeks"] == 2


def test_the_cli_documents_its_flags(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        job.main(["--help"])
    usage = capsys.readouterr().out
    assert "--data-source" in usage and "--today" in usage and "--push" in usage


@pytest.mark.anyio
@pytest.mark.services
async def test_the_job_reads_sold_lines_by_order_kind_and_m6_reads_a_delivery_day(
    db_session: AsyncSession,
) -> None:
    from gharbasket_sim.run import simulate_world
    from sqlalchemy import select

    from gharbasket.modules.intel_gateway import api as gateway
    from gharbasket.modules.intel_gateway.router import ForecastsIn, put_forecasts
    from gharbasket.modules.orders.models import Order, OrderLine
    from gharbasket.platform.models import OutboxEvent
    from gharbasket.tools.seed import seed_catalog, seed_delivery, seed_households, seed_societies

    await seed_societies(db_session)
    await seed_households(db_session)
    await seed_catalog(db_session)
    await seed_delivery(db_session)
    await simulate_world(
        db_session,
        seed=7,
        weeks=1,
        start=date(2026, 10, 5),
        planner="rules",
        adoption=0.02,
        write_db=True,
    )
    box = (
        await db_session.execute(
            select(Order).where(Order.kind == "box", Order.status.in_(job.SOLD)).limit(1)
        )
    ).scalar_one()
    line = (
        await db_session.execute(select(OrderLine).where(OrderLine.order_id == box.id).limit(1))
    ).scalar_one()

    def order(kind: str, status: str, source: str = "synthetic") -> Order:
        return Order(
            household_id=box.household_id,
            hub_id=box.hub_id,
            society_id=box.society_id,
            kind=kind,
            delivery_date=box.delivery_date,
            slot_id=box.slot_id,
            cutoff_at=box.cutoff_at,
            status=status,
            subtotal_paise=0,
            total_paise=0,
            data_source=source,
        )

    addon, refunded = order("addon", "delivered"), order("tiffin", "refunded")
    cancelled, real = order("addon", "cancelled"), order("addon", "delivered", "real")
    db_session.add_all([addon, refunded, cancelled, real])
    await db_session.flush()
    for o in (addon, refunded, cancelled, real):
        db_session.add(
            OrderLine(
                order_id=o.id,
                product_id=line.product_id,
                kind="addon",  # the line kind of an add-on order and of a box order's add-on
                qty=3,
                step_qty=line.step_qty,
                step_unit=line.step_unit,
                unit_price_paise=0,
            )
        )
    await db_session.flush()

    frame = await job.order_lines(db_session, "synthetic")

    kinds = dict(zip(frame["order_id"], frame["order_kind"], strict=True))
    assert kinds[str(addon.id)] == "addon" and kinds[str(refunded.id)] == "tiffin"
    assert kinds[str(box.id)] == "box"  # classified by the order, not the line
    assert str(cancelled.id) not in kinds and str(real.id) not in kinds
    assert (await job.order_lines(db_session, "pilot"))["order_id"].tolist() == [str(real.id)]

    today = forecast.monday_of(box.delivery_date) + timedelta(weeks=1)  # the next Monday
    series = forecast.weekly_series(frame, forecast.last_complete_week(today))
    hub = str(box.hub_id)
    planned, test = forecast.forecast_hub(series, frame, hub, today)
    assert planned and test.windows == 0  # one week of history: naive, no backtest yet
    days = {line.for_date for line in planned}
    assert all(today < d <= today + timedelta(days=7) for d in days)
    delivery_weekdays = {d.weekday() for d in frame[frame["hub_id"] == hub]["delivery_date"]}
    assert {d.weekday() for d in days} <= delivery_weekdays

    stored = await put_forecasts(
        ForecastsIn.model_validate(
            {
                "hub_id": hub,
                "model": f"forecast-{test.champion.lower()}-v0",
                "data_source": "synthetic",
                "items": forecast.as_rows(planned),
            }
        ),
        db_session,
    )
    assert stored.accepted == len(planned) and set(stored.delivery_dates) == days
    for day in days:  # M6 asks per delivery day, whatever the weekday
        kept = await gateway.hub_forecast(db_session, box.hub_id, day)
        assert kept is not None and kept["data_source"] == "synthetic"
        assert {item["for_date"] for item in kept["items"]} == {day.isoformat()}
    tuesday = today + timedelta(days=1)  # the simulator delivers on Tuesdays
    assert tuesday in days
    assert await gateway.hub_forecast(db_session, box.hub_id, tuesday) is not None
    event = (
        await db_session.execute(
            select(OutboxEvent).where(OutboxEvent.event_type == "intel.forecast_ready")
        )
    ).scalar_one()
    assert event.payload["hub_id"] == hub
    assert event.payload["delivery_dates"] == sorted(d.isoformat() for d in days)


@pytest.mark.anyio
@pytest.mark.services
async def test_exogenous_reads_the_calendar_and_prices_up_to_the_cutoff(
    db_session: AsyncSession,
) -> None:
    import uuid

    from gharbasket.modules.catalog.models import Festival
    from gharbasket.modules.intel_gateway.models import PriceIndex

    product = uuid.uuid4()
    monday = date(2031, 3, 3)  # far from any seeded calendar row
    series = pd.DataFrame(
        {
            "hub_id": "hub-1",
            "product_id": str(product),
            "ds": pd.to_datetime([monday + timedelta(weeks=w) for w in range(3)]),
            "committed": 0.0,
            "uncommitted": 1.0,
        }
    )
    names = {"en": "Test fast", "hi": "Test fast", "mr": "Test fast"}
    cutoff = monday + timedelta(weeks=2, days=6)  # the Sunday ending the last series week
    db_session.add_all(
        [
            Festival(
                day=monday + timedelta(days=2),
                code="t_fest",
                region="all",
                names=names,
                rules={"fasting": False},
            ),
            # in a forecast week, after the cut-off: the calendar is known in advance
            Festival(
                day=cutoff + timedelta(days=3),
                code="t_fast",
                region="all",
                names=names,
                rules={"fasting": True},
            ),
            PriceIndex(
                product_id=product,
                day=monday + timedelta(days=1),
                market="Vashi",
                modal_paise_per_kg=4000,
                index_4w=1.2,
            ),
            PriceIndex(
                product_id=product,
                day=monday + timedelta(days=3),
                market="Vashi",
                modal_paise_per_kg=4000,
                index_4w=1.4,
            ),
            PriceIndex(
                product_id=product,
                day=cutoff,
                market="Vashi",
                modal_paise_per_kg=4000,
                index_4w=1.1,
            ),
            # after the cut-off: a target week's price, never read
            PriceIndex(
                product_id=product,
                day=cutoff + timedelta(days=1),
                market="Vashi",
                modal_paise_per_kg=9000,
                index_4w=2.5,
            ),
        ]
    )
    await db_session.flush()

    exog = await job.exogenous(db_session, series, cutoff)

    assert exog.prices is not None and exog.weeks is not None
    prices = {
        (row["unique_id"], row["ds"].date()): row["price_index"]
        for row in exog.prices.to_dict("records")
    }
    assert prices == {
        (str(product), monday): pytest.approx(1.3),
        (str(product), monday + timedelta(weeks=2)): pytest.approx(1.1),
    }
    weeks = {
        row["ds"].date(): (row["festival_days"], row["fast_days"])
        for row in exog.weeks.to_dict("records")
    }
    assert weeks[monday] == (1.0, 0.0)
    assert weeks[monday + timedelta(weeks=3)] == (1.0, 1.0)
    assert (await job.exogenous(db_session, series.iloc[0:0], cutoff)).prices is None
