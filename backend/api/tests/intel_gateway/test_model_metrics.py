"""`POST /v1/internal/intel/model-metrics`: the eval harness stores its rows (operator token)."""

import httpx2
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.intel_gateway import api as gateway

pytestmark = [pytest.mark.anyio, pytest.mark.services]


async def test_rows_are_stored_and_the_latest_read_back(
    api: httpx2.AsyncClient, db_session: AsyncSession
) -> None:
    row = {
        "model_name": "planner_cpsat",
        "version": "abc1234",
        "metric": "acceptance",
        "value": 0.81,
        "split": "holdout",
        "data_source": "synthetic",
        "details": {"seeds": [7, 11], "ci95": 0.02},
    }
    first = await api.post(
        "/v1/internal/intel/model-metrics",
        json={"items": [row, {**row, "metric": "distinct_perishables", "value": 9.5}]},
    )
    newer = await api.post(
        "/v1/internal/intel/model-metrics",
        json={"items": [{**row, "version": "def5678", "value": 0.84}]},
    )
    bad = await api.post(
        "/v1/internal/intel/model-metrics",
        json={"items": [{**row, "split": "random"}]},
    )

    assert (first.status_code, first.json()) == (200, {"accepted": 2})
    assert newer.json() == {"accepted": 1} and bad.status_code == 422
    latest = await gateway.latest_metrics(db_session, "planner_cpsat")
    by_metric = {m.metric: m for m in latest}
    assert by_metric["acceptance"].value == 0.84 and by_metric["acceptance"].version == "def5678"
    assert by_metric["distinct_perishables"].value == 9.5
    assert by_metric["acceptance"].details == {"seeds": [7, 11], "ci95": 0.02}
