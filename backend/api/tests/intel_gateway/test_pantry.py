"""The pantry twin through the gateway (M10 §3.3): the ML service, the fallback's closed form,
the recorded prediction."""

import uuid
from datetime import date

import httpx2
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.intel_gateway import api, fallback
from gharbasket.modules.intel_gateway.schemas import PantryItem, PantryObservation, PantryRequest

from ..conftest import make_settings
from .test_plan_week import TOKEN, ml_transport

pytestmark = pytest.mark.anyio


def request(*observations: tuple[float, str | None]) -> PantryRequest:
    return PantryRequest(
        items=[
            PantryItem(
                product="milk",
                step=500,
                prior_mean=1400,
                observations=[PantryObservation(qty=q, signal=s) for q, s in observations],  # pyright: ignore[reportArgumentType]
            )
        ]
    )


def test_the_fallback_gives_the_same_mean_as_the_service() -> None:
    answer = fallback.pantry_quantities(request((2000, "ran_out"), (1800, None)))
    item = answer.items[0]
    assert answer.source == "fallback" and item.weeks_observed == 2
    # alpha = 2·1400/500 + (2000·1.3 + 1800)/500 = 5.6 + 8.8 = 14.4; beta = 4 → mean 1800
    assert (item.alpha, item.beta, item.mean) == (14.4, 4.0, 1800.0)
    assert item.p10 < item.mean < item.p90
    assert fallback.pantry_quantities(request()).items[0].mean == 1400.0


async def test_the_service_answers_and_the_prediction_is_recorded(
    db_session: AsyncSession,
) -> None:
    settings = make_settings(ml_service_url="http://ml", ml_service_token=TOKEN)
    household = uuid.uuid4()
    async with httpx2.AsyncClient(transport=ml_transport(), base_url="http://ml") as http:
        answer = await api.pantry_quantities(
            http,
            settings,
            request((2000, None)),
            session=db_session,
            household_id=household,
            for_date=date(2026, 10, 13),
        )
    assert answer.source == "ml" and answer.model == "pantry-gamma-poisson-v0"
    assert answer.items[0].mean == fallback.pantry_quantities(request((2000, None))).items[0].mean
    kept = await api.latest_prediction(
        db_session, kind="pantry", subject_id=household, for_date=date(2026, 10, 13)
    )
    assert (
        kept is not None and kept.source == "ml" and kept.payload["items"][0]["product"] == "milk"
    )


async def test_without_the_service_the_fallback_answers() -> None:
    settings = make_settings(ml_service_url="http://down.invalid", ml_timeout_s=0.5)
    async with httpx2.AsyncClient() as http:
        answer = await api.pantry_quantities(http, settings, request())
    assert answer.source == "fallback"
