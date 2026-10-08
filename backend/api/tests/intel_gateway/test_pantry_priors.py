"""`POST /v1/internal/intel/pantry-priors`: a promoted version's cohort priors (per adult
equivalent) replace the stored ones, kept per (cohort, product), and M3 reads them through the
facade (M10 §3.3 v1)."""

import httpx2
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.intel_gateway import api as gateway
from gharbasket.modules.subscriptions import rules

pytestmark = [pytest.mark.anyio, pytest.mark.services]


def test_cohorts_by_adult_equivalents() -> None:
    assert rules.cohort_of(1.0) == "small" and rules.cohort_of(2.0) == "medium"
    assert rules.cohort_of(3.5) == "large"


async def test_a_promoted_version_replaces_the_priors_read_per_cohort(
    api: httpx2.AsyncClient, db_session: AsyncSession, world: None
) -> None:
    from gharbasket.modules.catalog import api as catalog_api

    ids = await catalog_api.product_ids_by_slug(db_session, ["milk", "curd"])
    assert set(ids) == {"milk", "curd"}
    first = await api.post(
        "/v1/internal/intel/pantry-priors",
        json={
            "version": "2026-10-05",
            "items": [
                {
                    "cohort": "small",
                    "product": "milk",
                    "prior_mean_per_ae": 3500,
                    "prior_weeks": 2.5,
                },
                {
                    "cohort": "medium",
                    "product": "milk",
                    "prior_mean_per_ae": 7000,
                    "prior_weeks": 3.0,
                },
                {
                    "cohort": "small",
                    "product": "curd",
                    "prior_mean_per_ae": 900,
                    "prior_weeks": 1.5,
                },
                {
                    "cohort": "small",
                    "product": "no-such-thing",
                    "prior_mean_per_ae": 1,
                    "prior_weeks": 1,
                },
            ],
        },
    )
    assert first.status_code == 200, first.text
    assert first.json() == {"accepted": 3, "unknown_slugs": ["no-such-thing"]}
    assert await gateway.pantry_priors(db_session, "medium") == {ids["milk"]: (7000.0, 3.0)}
    newer = await api.post(
        "/v1/internal/intel/pantry-priors",
        json={
            "version": "2026-10-12",
            "items": [
                {
                    "cohort": "small",
                    "product": "milk",
                    "prior_mean_per_ae": 3800,
                    "prior_weeks": 4.0,
                }
            ],
        },
    )
    assert newer.json()["accepted"] == 1
    bad = await api.post(
        "/v1/internal/intel/pantry-priors",
        json={
            "version": "x",
            "items": [{"cohort": "huge", "product": "milk", "prior_mean_per_ae": 1}],
        },
    )
    assert bad.status_code == 422

    # The newer version replaces the older one whole: nothing of 2026-10-05 lingers.
    assert await gateway.pantry_priors(db_session, "small") == {ids["milk"]: (3800.0, 4.0)}
    assert await gateway.pantry_priors(db_session, "medium") == {}
    assert await gateway.pantry_priors(db_session, "large") == {}
