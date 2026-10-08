"""`python -m gharbasket_training.jobs.forecast` (T-1 21:10 IST from Actions, M6 §6): the
uncommitted demand per hub x product x delivery day for the 7 days after the run, with newsvendor
order quantities, from the module tables (read-only role), handed to
`POST /v1/internal/intel/forecasts`; the backtest's metrics per model go to
`POST /v1/internal/intel/model-metrics`. v1 adds what is known in advance as LightGBM features:
the festival calendar (M2) and the mandi price index (`intel.price_index`).

- Only complete weeks are used: the run's current week is still filling (tomorrow's boxes have
  just locked), so it is left out of training and of `committed`.
- The model is weekly; each product's weekly q50 and q_tau are split over the hub's delivery
  days by each weekday's share of the product's units in the last 4 complete weeks (the hub's
  share of orders per weekday when the product sold nothing then). One line per hub x product x
  delivery day; `for_date` is the delivery day, so M6 reads `hub_forecast(hub, delivery_date)`.
- One data source per run (`--data-source`, synthetic until the pilot): orders of other sources
  are never read, and every row pushed carries the label. Sources are never pooled.
- Metrics (eval protocol §3 and §4, split `backtest`): per model WAPE (2-week horizon, an origin
  every week), the coverage of q_tau, the pinball loss of q50 and q_tau, MASE, and the champion's
  WAPE gap to SeasonalNaive with the protocol's "10 points better" check as a reported result.

    uv run --directory backend/training python -m gharbasket_training.jobs.forecast \\
        [--push] [--tau 0.375] [--data-source synthetic] [--today 2026-10-08]
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo

import httpx2
import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.intel_gateway.models import PriceIndex
from gharbasket.modules.orders.models import STATUSES, Order, OrderLine
from gharbasket.platform.db import create_engine
from gharbasket_training import forecast
from gharbasket_training.settings import Settings

IST = ZoneInfo("Asia/Kolkata")
# Demand that happened: every status that has passed `locked`, whether or not the delivery went
# well (undelivered, redelivery and refunded orders were still bought for). Statuses before or
# off the locked path are not demand. A new status in orders.models must be placed in one of the
# two (a test checks they cover STATUSES exactly).
NOT_SOLD = ("approved", "payment_failed", "cancelled")
SOLD = tuple(s for s in STATUSES if s not in NOT_SOLD)
# The protocol's label → the orders table's `data_source` (the pilot's and later rows are "real").
ORDER_SOURCE = {"synthetic": "synthetic", "pilot": "real", "live": "real"}


async def order_lines(session: AsyncSession, data_source: str = "synthetic") -> pd.DataFrame:
    """Sold order lines of one data source, with the order's kind (box, addon, tiffin)."""
    rows = (
        await session.execute(
            select(
                Order.hub_id,
                OrderLine.product_id,
                Order.id,
                Order.delivery_date,
                Order.kind,
                OrderLine.qty,
            )
            .join(Order, Order.id == OrderLine.order_id)
            .where(Order.status.in_(SOLD), Order.data_source == ORDER_SOURCE[data_source])
        )
    ).all()
    return pd.DataFrame(
        [
            {
                "hub_id": str(hub),
                "product_id": str(product),
                "order_id": str(order),
                "delivery_date": day,
                "order_kind": kind,
                "qty": float(qty),
            }
            for hub, product, order, day, kind, qty in rows
        ],
        columns=forecast.LINE_COLUMNS,
    )


def weeks_frame(days: list[tuple[date, str, dict[str, Any]]]) -> pd.DataFrame:
    """Festival days per week (Monday) and how many of them are fasts."""
    festival: dict[date, set[date]] = {}
    fasting: dict[date, set[date]] = {}
    for day, _code, rules in days:
        monday = forecast.monday_of(day)
        festival.setdefault(monday, set()).add(day)
        if rules.get("fasting"):
            fasting.setdefault(monday, set()).add(day)
    return pd.DataFrame(
        [
            {
                "ds": pd.Timestamp(monday),
                "festival_days": float(len(days_)),
                "fast_days": float(len(fasting.get(monday, ()))),
            }
            for monday, days_ in sorted(festival.items())
        ],
        columns=["ds", "festival_days", "fast_days"],
    )


def prices_frame(rows: Sequence[tuple[Any, date, float]]) -> pd.DataFrame:
    """The price index per product x week (Monday), averaged over the week's markets and days."""
    if not rows:
        return pd.DataFrame(columns=["unique_id", "ds", "price_index"])
    df = pd.DataFrame(
        [
            {"unique_id": str(pid), "ds": pd.Timestamp(forecast.monday_of(day)), "price_index": ix}
            for pid, day, ix in rows
        ]
    )
    return cast(pd.DataFrame, df.groupby(["unique_id", "ds"], as_index=False)["price_index"].mean())


async def exogenous(session: AsyncSession, series: pd.DataFrame) -> forecast.Exog:
    """The festival calendar and the price index over the series' weeks."""
    if series.empty:
        return forecast.Exog()
    weeks = [cast(pd.Timestamp, pd.Timestamp(ds)).date() for ds in series["ds"]]
    first, last = min(weeks), max(weeks) + timedelta(weeks=forecast.HORIZON + 1)
    days = await catalog_api.festivals_between(session, first, last)
    rows = (
        await session.execute(
            select(PriceIndex.product_id, PriceIndex.day, PriceIndex.index_4w).where(
                PriceIndex.day >= first
            )
        )
    ).all()
    return forecast.Exog(
        weeks=weeks_frame([(d, c, dict(r)) for d, c, r in days]),
        prices=prices_frame([(p, d, float(i)) for p, d, i in rows]),
    )


def metric_rows(test: forecast.Backtest, commit: str, data_source: str) -> list[dict[str, Any]]:
    """The backtest as `intel.model_metrics` rows (split `backtest`), one per model and metric."""
    details: dict[str, Any] = {
        "hub_id": test.hub_id,
        "windows": test.windows,
        "series": test.series,
        "horizon_weeks": forecast.HORIZON,
        "tau": round(test.tau, 4),
        "eval_points": test.eval_points,
        "champion": test.champion,
    }
    measured = {
        "wape": test.wape,
        "coverage_qtau": test.coverage,
        "pinball_q50": test.pinball_q50,
        "pinball_qtau": test.pinball_qtau,
        "mase": test.mase,
    }
    rows = [
        {
            "model_name": f"forecast_{model.lower()}",
            "version": commit,
            "metric": metric,
            "value": value,
            "split": "backtest",
            "data_source": data_source,
            "details": details,
        }
        for metric, values in measured.items()
        for model, value in values.items()
    ]
    gap = test.gap_to_seasonal_naive
    if gap is not None:
        rows.append(
            {
                "model_name": f"forecast_{test.champion.lower()}",
                "version": commit,
                "metric": "wape_gap_seasonal_naive",
                "value": gap,
                "split": "backtest",
                "data_source": data_source,
                "details": {
                    **details,
                    "protocol_gap": forecast.PROTOCOL_GAP,
                    "meets_protocol_gap": test.meets_protocol_gap,
                },
            }
        )
    return rows


async def push(
    http: httpx2.AsyncClient,
    settings: Settings,
    hub_id: str,
    lines: list[forecast.Line],
    test: forecast.Backtest,
    commit: str,
    data_source: str,
) -> int:
    if not settings.api_url or settings.jobs_token is None:
        raise RuntimeError("set API_URL and JOBS_TOKEN to push the forecast")
    headers = {"Authorization": f"Bearer {settings.jobs_token.get_secret_value()}"}
    base = settings.api_url.rstrip("/")
    accepted = 0
    if lines:
        answer = await http.post(
            f"{base}/v1/internal/intel/forecasts",
            json={
                "hub_id": hub_id,
                "model": f"forecast-{test.champion.lower()}-v1",
                "data_source": data_source,
                "items": forecast.as_rows(lines),
            },
            headers=headers,
            timeout=settings.http_timeout_s,
        )
        answer.raise_for_status()
        accepted = int(answer.json()["accepted"])
    rows = metric_rows(test, commit, data_source)
    if rows:
        metrics = await http.post(
            f"{base}/v1/internal/intel/model-metrics",
            json={"items": rows},
            headers=headers,
            timeout=settings.http_timeout_s,
        )
        metrics.raise_for_status()
    return accepted


def summarise(hub_id: str, planned: list[forecast.Line], test: forecast.Backtest) -> dict[str, Any]:
    return {
        "hub_id": hub_id,
        "lines": len(planned),
        "days": sorted({line.for_date.isoformat() for line in planned}),
        "champion": test.champion,
        "windows": test.windows,
        "wape": test.wape,
        "coverage_qtau": test.coverage,
        "pinball_q50": test.pinball_q50,
        "pinball_qtau": test.pinball_qtau,
        "mase": test.mase,
        "gap_to_seasonal_naive": test.gap_to_seasonal_naive,
        "meets_protocol_gap": test.meets_protocol_gap,
    }


async def run(settings: Settings, args: argparse.Namespace) -> list[dict[str, Any]]:
    url = settings.database_url_ro or settings.database_url
    if url is None:
        raise RuntimeError("set DATABASE_URL_RO (or DATABASE_URL) to read the database")
    engine = create_engine(url.get_secret_value(), pooled=False)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            lines = await order_lines(session, args.data_source)
            today: date = args.today
            series = forecast.weekly_series(lines, forecast.last_complete_week(today))
            exog = await exogenous(session, series)
    finally:
        await engine.dispose()
    costs = forecast.Costs(underage=args.tau, overage=1 - args.tau)
    out = []
    async with httpx2.AsyncClient() as http:
        for hub_id in sorted(str(h) for h in series["hub_id"].unique()):
            planned, test = forecast.forecast_hub(series, lines, hub_id, today, costs, exog)
            summary = summarise(hub_id, planned, test)
            if args.push:
                summary["pushed"] = await push(
                    http, settings, hub_id, planned, test, args.commit, args.data_source
                )
            out.append(summary)
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Forecast the uncommitted demand per hub, product and delivery day."
    )
    parser.add_argument("--tau", type=float, default=0.375, help="C_u / (C_u + C_o)")
    parser.add_argument("--push", action="store_true", help="store through the API")
    parser.add_argument("--commit", default=datetime.now(UTC).strftime("%Y%m%d"))
    parser.add_argument(
        "--data-source",
        choices=sorted(ORDER_SOURCE),
        default="synthetic",
        help="whose orders to read and the label of every row (never pooled)",
    )
    parser.add_argument(
        "--today",
        type=date.fromisoformat,
        default=datetime.now(IST).date(),
        help="the run's day (IST); the forecast covers the 7 days after it",
    )
    args = parser.parse_args(argv)
    for summary in asyncio.run(run(Settings(), args)):
        print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
