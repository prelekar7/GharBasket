# The pandas stubs are too loose for these frames; the tests hold the shapes to account.
# pyright: reportAttributeAccessIssue=false, reportReturnType=false, reportCallIssue=false
# pyright: reportArgumentType=false, reportGeneralTypeIssues=false
"""`python -m gharbasket_training.jobs.forecast` (T-1 21:10 IST from Actions, M6 §6): next
week's uncommitted demand per hub x product with newsvendor order quantities, from the module
tables (read-only role), handed to `POST /v1/internal/intel/forecasts`; the backtest's WAPE per
model goes to `POST /v1/internal/intel/model-metrics`.

    uv run --directory backend/training python -m gharbasket_training.jobs.forecast \\
        [--push] [--tau 0.375]
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import httpx2
import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.orders.models import Order, OrderLine
from gharbasket.platform.db import create_engine
from gharbasket_training import forecast
from gharbasket_training.settings import Settings

SOLD = ("locked", "picking", "packed", "dispatched", "at_society", "delivered")


async def order_lines(session: AsyncSession) -> pd.DataFrame:
    rows = (
        await session.execute(
            select(
                Order.hub_id,
                OrderLine.product_id,
                Order.delivery_date,
                OrderLine.kind,
                OrderLine.qty,
            )
            .join(Order, Order.id == OrderLine.order_id)
            .where(Order.status.in_(SOLD))
        )
    ).all()
    return pd.DataFrame(
        [
            {
                "hub_id": str(hub),
                "product_id": str(product),
                "delivery_date": day,
                "kind": kind,
                "qty": float(qty),
            }
            for hub, product, day, kind, qty in rows
        ],
        columns=["hub_id", "product_id", "delivery_date", "kind", "qty"],
    )


async def push(
    http: httpx2.AsyncClient,
    settings: Settings,
    hub_id: str,
    lines: list[forecast.Line],
    test: forecast.Backtest,
    commit: str,
) -> int:
    if not settings.api_url or settings.jobs_token is None:
        raise RuntimeError("set API_URL and JOBS_TOKEN to push the forecast")
    headers = {"Authorization": f"Bearer {settings.jobs_token.get_secret_value()}"}
    base = settings.api_url.rstrip("/")
    answer = await http.post(
        f"{base}/v1/internal/intel/forecasts",
        json={
            "hub_id": hub_id,
            "model": f"forecast-{test.champion.lower()}-v0",
            "items": forecast.as_rows(lines),
        },
        headers=headers,
        timeout=settings.http_timeout_s,
    )
    answer.raise_for_status()
    if test.wape:
        metrics = await http.post(
            f"{base}/v1/internal/intel/model-metrics",
            json={
                "items": [
                    {
                        "model_name": f"forecast_{m.lower()}",
                        "version": commit,
                        "metric": "wape",
                        "value": w,
                        "split": "backtest",
                        "data_source": "synthetic",
                        "details": {
                            "hub_id": hub_id,
                            "windows": test.windows,
                            "series": test.series,
                        },
                    }
                    for m, w in test.wape.items()
                ]
            },
            headers=headers,
            timeout=settings.http_timeout_s,
        )
        metrics.raise_for_status()
    return int(answer.json()["accepted"])


async def run(settings: Settings, args: argparse.Namespace) -> list[dict[str, Any]]:
    url = settings.database_url_ro or settings.database_url
    if url is None:
        raise RuntimeError("set DATABASE_URL_RO (or DATABASE_URL) to read the database")
    engine = create_engine(url.get_secret_value(), pooled=False)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            lines = await order_lines(session)
    finally:
        await engine.dispose()
    series = forecast.weekly_series(lines)
    costs = forecast.Costs(underage=args.tau, overage=1 - args.tau)
    out = []
    async with httpx2.AsyncClient() as http:
        for hub_id in sorted(series["hub_id"].unique()):
            planned, test = forecast.forecast_hub(series, hub_id, costs)
            summary: dict[str, Any] = {
                "hub_id": hub_id,
                "products": len(planned),
                "champion": test.champion,
                "wape": test.wape,
                "windows": test.windows,
            }
            if args.push:
                summary["pushed"] = await push(http, settings, hub_id, planned, test, args.commit)
            out.append(summary)
    return out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Forecast next week's uncommitted demand.")
    parser.add_argument("--tau", type=float, default=0.375, help="C_u / (C_u + C_o)")
    parser.add_argument("--push", action="store_true", help="store through the API")
    parser.add_argument("--commit", default=datetime.now(UTC).strftime("%Y%m%d"))
    args = parser.parse_args(argv)
    for summary in asyncio.run(run(Settings(), args)):
        print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
