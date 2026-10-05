"""Scheduled jobs of M10's gateway (spec §6): `monitor_drift`, daily, scores what the models said
against what happened and keeps the result in `intel.model_metrics` for the console's AI health
table (handbook 08 §8.1 monitoring).

- Forecasts (kind `forecast`, one row per hub and delivery day): realised WAPE of the
  uncommitted quantity per product against the add-on and tiffin lines of the orders that
  reached the cut-off; `drift` = 1 when it is more than DRIFT_POINTS worse than the same
  model's latest backtest WAPE.
- Plans: the share of the day's offered weeks that were approved or locked, per plan source
  (`ml`, `fallback`).

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
DRIFT_POINTS = 0.10
UNCOMMITTED_KINDS = ("addon", "tiffin")


def metric_model_name(prediction_model: str) -> str:
    """`forecast-lightgbm-v1` → `forecast_lightgbm`, the name the backtest rows use."""
    parts = prediction_model.split("-")
    if len(parts) >= 2 and parts[-1].startswith("v"):
        parts = parts[:-1]
    return "_".join(parts).lower()


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
        model_name = metric_model_name(prediction.model_name)
        hub_id = prediction.subject_id
        if await service.scored_already(ctx.session, model_name, "wape", day, hub_id):
            continue
        actual: dict[str, float] = {}
        for line in await orders_api.sold_lines(ctx.session, prediction.subject_id, day):
            if line.kind in UNCOMMITTED_KINDS:
                actual[str(line.product_id)] = actual.get(str(line.product_id), 0.0) + line.qty
        predicted = {
            str(item["product_id"]): float(item["uncommitted_q50"])
            for item in prediction.payload.get("items", [])
        }
        realised = wape(actual, predicted)
        baseline = next(
            (
                m.value
                for m in await service.latest_metrics(ctx.session, model_name)
                if m.metric == "wape" and m.split == "backtest"
            ),
            None,
        )
        drift = baseline is not None and realised - baseline > DRIFT_POINTS
        await service.record_metrics(
            ctx.session,
            model_name=model_name,
            version=prediction.model_version or prediction.model_name,
            metrics={"wape": realised, "drift": 1.0 if drift else 0.0},
            split="live",
            data_source="mixed",
            details={
                "hub_id": str(prediction.subject_id),
                "for_date": day.isoformat(),
                "products": len(predicted),
                "backtest_wape": baseline,
            },
        )
        scored += 1
        if drift:
            drifted += 1
            log.warning(
                "model_drift", model=model_name, hub_id=str(prediction.subject_id), wape=realised
            )
    return scored, drifted


async def score_plans(ctx: JobContext, day: date) -> int:
    scored = 0
    for source, (accepted, offered) in (
        await subscriptions_api.acceptance_on(ctx.session, day)
    ).items():
        model_name = f"planner_{source}"
        if not offered or await service.scored_already(
            ctx.session, model_name, "acceptance", day, None
        ):
            continue
        await service.record_metrics(
            ctx.session,
            model_name=model_name,
            version=day.isoformat(),
            metrics={"acceptance": round(accepted / offered, 4)},
            split="live",
            data_source="mixed",
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
