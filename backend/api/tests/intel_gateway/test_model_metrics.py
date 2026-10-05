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


async def test_feature_snapshots_are_stored_as_of_a_cut_off(
    api: httpx2.AsyncClient, db_session: AsyncSession
) -> None:
    import uuid
    from datetime import UTC, datetime

    from sqlalchemy import select

    from gharbasket.modules.intel_gateway.models import FeatureSnapshot

    household = uuid.uuid4()
    body = {
        "as_of": "2026-10-09T00:00:00Z",
        "items": [
            {"subject_type": "household", "subject_id": str(household), "features": {"weeks": 1}}
        ],
    }
    first = await api.post("/v1/internal/intel/feature-snapshots", json=body)
    body["items"][0]["features"] = {"weeks": 2}
    again = await api.post("/v1/internal/intel/feature-snapshots", json=body)
    bad = await api.post(
        "/v1/internal/intel/feature-snapshots",
        json={**body, "items": [{**body["items"][0], "subject_type": "x"}]},
    )

    assert first.json() == {"accepted": 1} and again.json() == {"accepted": 1}
    assert bad.status_code == 422
    rows = (await db_session.execute(select(FeatureSnapshot))).scalars().all()
    assert len(rows) == 1 and rows[0].features == {"weeks": 2}  # the same as_of replaces
    assert rows[0].as_of == datetime(2026, 10, 9, tzinfo=UTC)


async def test_forecasts_are_stored_per_hub_and_day_and_read_back(
    api: httpx2.AsyncClient, db_session: AsyncSession
) -> None:
    import uuid
    from datetime import date

    hub, product = uuid.uuid4(), uuid.uuid4()
    item = {
        "product_id": str(product),
        "for_date": "2026-10-20",
        "committed": 12,
        "uncommitted_q50": 1.5,
        "uncommitted_qtau": 3.0,
        "order_qty": 15.0,
        "model": "AutoETS",
    }
    stored = await api.post(
        "/v1/internal/intel/forecasts",
        json={"hub_id": str(hub), "model": "forecast-autoets-v0", "items": [item]},
    )
    assert stored.json() == {"accepted": 1}
    kept = await gateway.hub_forecast(db_session, hub, date(2026, 10, 20))
    assert kept is not None and kept["model"] == "forecast-autoets-v0"
    assert kept["items"][0]["order_qty"] == 15.0 and kept["items"][0]["product_id"] == str(product)
    assert await gateway.hub_forecast(db_session, hub, date(2026, 10, 27)) is None
