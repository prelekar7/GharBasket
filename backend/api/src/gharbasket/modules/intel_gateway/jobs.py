"""Scheduled jobs of M10's gateway (spec §6): `monitor_drift`, daily, scores what the models said
against what happened and keeps the result in `intel.model_metrics` for the console's AI health
table (handbook 08 §8.1 monitoring).

- Forecasts (kind `forecast`, one row per hub, delivery day and data source; `for_date` is the
  delivery day): realised WAPE of that day's uncommitted q50 per product against that day's
  sold lines of add-on and tiffin orders (`orders.api.sold_lines`: every status past `locked`,
  classified by the order's kind, as the forecast job counts them), read from the orders of the
  forecast's own data source (`synthetic`, or `real` for `pilot`/`live`). `drift` = 1 when the
  realised WAPE is more than DRIFT_POINTS worse than the newest backtest WAPE of the same model,
  hub and data source; without such a backtest only `wape` is written.
- Plans: the share of the day's offered weeks that were approved or locked, per plan source
  (`ml`, `fallback`) and per data source (a `real` cycle is labelled `settings.real_data_source`).
- Rows carry the data source they were measured on; nothing is ever written as `mixed`.

Waste against the forecast waits for M6's receiving and cascade counts.
"""

from datetime import date, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import structlog

from gharbasket.modules.intel_gateway import service
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.subscriptions import api as subscriptions_api
from gharbasket.platform.jobs import JobContext, job

log = structlog.get_logger("gharbasket.intel")
IST = ZoneInfo("Asia/Kolkata")
LOOKBACK_DAYS = 7
DRIFT_POINTS = 0.10  # eval protocol §4 monitoring: WAPE worse than the backtest by > 10 points
UNCOMMITTED_KINDS = ("addon", "tiffin")
# The eval protocol's label → the orders table's `data_source` (as the forecast job's flag).
ORDER_SOURCE = {"synthetic": "synthetic", "pilot": "real", "live": "real"}


def metric_model_name(prediction_model: str) -> str:
    """`forecast-lightgbm-v1` → `forecast_lightgbm`, the name the backtest rows use."""
    parts = prediction_model.split("-")
    if len(parts) >= 2 and parts[-1].startswith("v"):
        parts = parts[:-1]
    return "_".join(parts).lower()


def label_of(row_source: str, real_label: str) -> str:
    """A row's `data_source` (real | synthetic) as the protocol's label."""
    return "synthetic" if row_source == "synthetic" else real_label


def wape(actual: dict[str, float], predicted: dict[str, float]) -> float:
    keys = set(actual) | set(predicted)
    total = sum(abs(actual.get(k, 0.0)) for k in keys)
    error = sum(abs(actual.get(k, 0.0) - predicted.get(k, 0.0)) for k in keys)
    if total == 0:
        return 0.0 if error == 0 else 1.0
    return round(error / total, 4)


async def score_forecasts(ctx: JobContext, day: date) -> tuple[int, int]:
    scored = drifted = 0
    for prediction in await service.forecasts_on(ctx.session, day):
        label = prediction.payload.get("data_source")
        hub_id = prediction.subject_id
        if label not in ORDER_SOURCE:  # a forecast stored before sources were recorded
            log.warning("forecast_without_data_source", hub_id=str(hub_id), for_date=str(day))
            continue
        model_name = metric_model_name(prediction.model_name)
        if await service.scored_already(ctx.session, model_name, "wape", day, hub_id, label):
            continue
        actual: dict[str, float] = {}
        for line in await orders_api.sold_lines(ctx.session, hub_id, day, ORDER_SOURCE[label]):
            if line.order_kind in UNCOMMITTED_KINDS:
                key = str(line.product_id)
                actual[key] = actual.get(key, 0.0) + line.qty
        predicted: dict[str, float] = {}
        for item in prediction.payload.get("items", []):
            if str(item.get("for_date", day.isoformat())) != day.isoformat():
                continue  # only this delivery day's share of the weekly forecast
            key = str(item["product_id"])
            predicted[key] = predicted.get(key, 0.0) + float(item["uncommitted_q50"])
        realised = wape(actual, predicted)
        baseline = await service.latest_backtest(ctx.session, model_name, "wape", hub_id, label)
        metrics = {"wape": realised}
        drift = baseline is not None and realised - baseline.value > DRIFT_POINTS
        if baseline is not None:
            metrics["drift"] = 1.0 if drift else 0.0
        await service.record_metrics(
            ctx.session,
            model_name=model_name,
            version=prediction.model_version or prediction.model_name,
            metrics=metrics,
            split="live",
            data_source=label,
            details={
                "hub_id": str(hub_id),
                "for_date": day.isoformat(),
                "products": len(predicted),
                "sold_units": round(sum(actual.values()), 2),
                "backtest_wape": None if baseline is None else baseline.value,
                "backtest_version": None if baseline is None else baseline.version,
            },
        )
        scored += 1
        if drift:
            drifted += 1
            log.warning(
                "model_drift", model=model_name, hub_id=str(hub_id), source=label, wape=realised
            )
    return scored, drifted


async def score_plans(ctx: JobContext, day: date) -> int:
    scored = 0
    real_label = ctx.settings.real_data_source
    acceptance = await subscriptions_api.acceptance_on(ctx.session, day)
    for (plan_source, row_source), (accepted, offered) in sorted(acceptance.items()):
        model_name = f"planner_{plan_source}"
        label = label_of(row_source, real_label)
        if not offered or await service.scored_already(
            ctx.session, model_name, "acceptance", day, None, label
        ):
            continue
        await service.record_metrics(
            ctx.session,
            model_name=model_name,
            version=day.isoformat(),
            metrics={"acceptance": round(accepted / offered, 4)},
            split="live",
            data_source=label,
            details={"for_date": day.isoformat(), "offered": offered, "accepted": accepted},
        )
        scored += 1
    return scored


@job("monitor_drift", "Score last week's forecasts and plans against what happened; flag drift")
async def monitor_drift(ctx: JobContext) -> dict[str, Any]:
    today = ctx.scheduled_for.astimezone(IST).date()
    totals = {"days": LOOKBACK_DAYS, "forecasts_scored": 0, "drifted": 0, "plans_scored": 0}
    for back in range(1, LOOKBACK_DAYS + 1):
        day = today - timedelta(days=back)
        scored, drifted = await score_forecasts(ctx, day)
        totals["forecasts_scored"] += scored
        totals["drifted"] += drifted
        totals["plans_scored"] += await score_plans(ctx, day)
    return totals
