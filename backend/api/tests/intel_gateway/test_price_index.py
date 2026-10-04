"""The mandi collector's drop-off point and the planner's price-spike lookup."""

from datetime import date

import httpx2
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.main import create_app
from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.intel_gateway import api as gateway
from gharbasket.modules.intel_gateway.models import PriceIndex
from gharbasket.tools.seed import seed_catalog

from ..conftest import DEPLOYED_SECRETS, make_settings

pytestmark = [pytest.mark.anyio, pytest.mark.services]


def item(slug: str, day: str, index: float, market: str = "Mumbai", modal: int = 2400) -> dict:
    return {
        "slug": slug,
        "day": day,
        "market": market,
        "modal_paise_per_kg": modal,
        "index_4w": index,
    }


async def test_the_index_is_stored_per_product_day_and_market_and_read_back(
    api: httpx2.AsyncClient, db_session: AsyncSession
) -> None:
    await seed_catalog(db_session)
    first = await api.post(
        "/v1/internal/intel/price-index",
        json={
            "items": [
                item("tomato", "2026-10-04", 1.62),
                item("tomato", "2026-10-04", 1.5, market="Vashi New Mumbai", modal=2600),
                item("onion", "2026-10-04", 1.0, modal=3100),
                item("jackfruit", "2026-10-04", 1.0),
            ]
        },
    )
    again = await api.post(
        "/v1/internal/intel/price-index",
        json={"items": [item("tomato", "2026-10-04", 1.7)]},
    )

    assert first.json() == {"accepted": 3, "unknown_slugs": ["jackfruit"]}
    assert again.json() == {"accepted": 1, "unknown_slugs": []}
    ids = await catalog_api.product_ids_by_slug(db_session, ["tomato", "onion", "palak"])
    rows = (await db_session.execute(select(PriceIndex))).scalars().all()
    assert len(rows) == 3  # the replay updated, never duplicated
    spikes = await gateway.price_spikes(db_session, list(ids.values()), date(2026, 10, 6))
    assert spikes == {ids["tomato"]: round((1.7 + 1.5) / 2, 3), ids["onion"]: 1.0}
    assert await gateway.price_spikes(db_session, [ids["tomato"]], date(2026, 10, 20)) == {}
    assert await gateway.price_spikes(db_session, [], date(2026, 10, 6)) == {}


async def test_the_latest_day_wins_and_bad_rows_are_422(
    api: httpx2.AsyncClient, db_session: AsyncSession
) -> None:
    await seed_catalog(db_session)
    await api.post(
        "/v1/internal/intel/price-index",
        json={"items": [item("tomato", "2026-10-01", 2.0), item("tomato", "2026-10-03", 1.2)]},
    )
    ids = await catalog_api.product_ids_by_slug(db_session, ["tomato"])

    assert await gateway.price_spikes(db_session, [ids["tomato"]], date(2026, 10, 4)) == {
        ids["tomato"]: 1.2
    }
    bad = await api.post(
        "/v1/internal/intel/price-index", json={"items": [item("tomato", "2026-10-01", 0)]}
    )
    assert bad.status_code == 422


def test_the_endpoint_needs_the_operator_token_when_deployed() -> None:
    settings = make_settings(app_env="staging", **DEPLOYED_SECRETS)
    with TestClient(create_app(settings)) as client:
        refused = client.post("/v1/internal/intel/price-index", json={"items": []})
    assert refused.status_code == 401
    assert refused.json()["code"] == "platform.operator_token_required"
