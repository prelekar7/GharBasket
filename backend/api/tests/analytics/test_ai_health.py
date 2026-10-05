"""`monitor_drift` scores a forecast against what was sold and flags drift; the console reads
`GET /v1/analytics/ai-health` (M12) with `analytics:read`."""

import uuid

import httpx2
import pytest
import time_machine

from gharbasket.modules.intel_gateway import jobs
from gharbasket.tools.seed import SEED_HUB_ID

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from ..subscriptions.test_cycles import NOW, PRIYA, as_user

pytestmark = pytest.mark.services

NEHA = "+919800001011"  # admin
YESTERDAY = "2026-10-09"  # NOW is Saturday 10 Oct 2026, 15:30 IST


def test_wape_and_the_metric_name() -> None:
    assert jobs.wape({"a": 10.0, "b": 0.0}, {"a": 8.0, "b": 2.0}) == 0.4
    assert jobs.wape({}, {"a": 3.0}) == 1.0 and jobs.wape({}, {}) == 0.0
    assert jobs.metric_model_name("forecast-lightgbm-v1") == "forecast_lightgbm"
    assert jobs.metric_model_name("forecast-autoets-v0") == "forecast_autoets"
    assert jobs.metric_model_name("rules") == "rules"


@pytest.mark.anyio
@pytest.mark.usefixtures("delivery")
@time_machine.travel(NOW, tick=False)
async def test_drift_is_scored_once_and_shown_on_the_health_table(
    clients: ClientFactory, google: FakeGoogle
) -> None:
    api: httpx2.AsyncClient = clients()
    backtest = await api.post(
        "/v1/internal/intel/model-metrics",
        json={
            "items": [
                {
                    "model_name": "forecast_lightgbm",
                    "version": "20261004",
                    "metric": "wape",
                    "value": 0.2,
                    "split": "backtest",
                    "data_source": "synthetic",
                    "details": {"hub_id": str(SEED_HUB_ID)},
                }
            ]
        },
    )
    assert backtest.status_code == 200
    stored = await api.post(
        "/v1/internal/intel/forecasts",
        json={
            "hub_id": str(SEED_HUB_ID),
            "model": "forecast-lightgbm-v1",
            "items": [
                {
                    "product_id": str(uuid.uuid4()),
                    "for_date": YESTERDAY,
                    "committed": 10,
                    "uncommitted_q50": 4,
                    "uncommitted_qtau": 6,
                    "order_qty": 16,
                    "model": "LightGBM",
                }
            ],
        },
    )
    assert stored.status_code == 200, stored.text

    # Nothing was sold on that day: WAPE 1.0, far worse than the backtest's 0.2 → drift.
    body = {"scheduled_for": NOW.isoformat()}
    ran = await api.post("/v1/internal/jobs/monitor_drift", json=body)
    assert ran.status_code == 200, ran.text
    assert ran.json()["summary"] == {
        "days": 7,
        "forecasts_scored": 1,
        "drifted": 1,
        "plans_scored": 0,
    }
    again = await api.post("/v1/internal/jobs/monitor_drift", json=body)
    assert again.json()["summary"]["forecasts_scored"] == 0  # scored once per hub and day

    neha, _ = await as_user(api, google, NEHA)
    health = await api.get("/v1/analytics/ai-health", headers=neha)
    assert health.status_code == 200, health.text
    table = health.json()
    assert table["window_days"] == 7 and health.headers["Cache-Control"] == "no-store"
    model = next(m for m in table["models"] if m["model_name"] == "forecast_lightgbm")
    assert model["drift"] is True
    live = {m["metric"]: m["value"] for m in model["metrics"] if m["split"] == "live"}
    assert live == {"wape": 1.0, "drift": 1.0}
    assert {(m["metric"], m["split"]) for m in model["metrics"]} >= {("wape", "backtest")}
    assert table["predictions"] == [{"kind": "forecast", "source": "ml", "count": 1}]

    priya, _ = await as_user(api, google, PRIYA)
    assert (await api.get("/v1/analytics/ai-health", headers=priya)).status_code == 403
