"""The pantry twin's closed form (M10 §3.3): prior, update, taps, intervals; the endpoint."""

import httpx2
import pytest
from gharbasket_ml import pantry
from gharbasket_ml.main import create_app
from gharbasket_ml.schemas import PantryItemIn, PantryObservation, PantryRequest
from gharbasket_ml.settings import Settings
from pydantic import SecretStr

TOKEN = "ml-test-token"


def item(*observations: tuple[float, str | None], prior: float = 1400.0) -> PantryItemIn:
    return PantryItemIn(
        product="milk",
        step=500,
        prior_mean=prior,
        prior_weeks=2,
        observations=[PantryObservation(qty=q, signal=s) for q, s in observations],  # pyright: ignore[reportArgumentType]
    )


def test_a_new_household_gets_its_cohort_prior_with_a_wide_interval() -> None:
    answer = pantry.quantities(PantryRequest(items=[item()])).items[0]
    assert answer.mean == 1400.0 and answer.weeks_observed == 0
    assert answer.p10 < 1400.0 < answer.p90 and answer.p90 - answer.p10 > 800


def test_observed_weeks_pull_the_mean_and_narrow_the_interval() -> None:
    few = pantry.quantities(PantryRequest(items=[item((2000, None), (2000, None))])).items[0]
    many = pantry.quantities(PantryRequest(items=[item(*([(2000, None)] * 10))])).items[0]
    assert 1400 < few.mean < many.mean < 2000
    assert many.p90 - many.p10 < few.p90 - few.p10
    assert many.weeks_observed == 10 and many.beta == 12.0


def test_taps_correct_the_box_quantity() -> None:
    plain = pantry.quantities(PantryRequest(items=[item((1500, "just_right"))])).items[0]
    short = pantry.quantities(PantryRequest(items=[item((1500, "ran_out"))])).items[0]
    waste = pantry.quantities(PantryRequest(items=[item((1500, "wasted"))])).items[0]
    assert waste.mean < plain.mean < short.mean


@pytest.mark.anyio
async def test_the_endpoint_answers_with_the_service_token() -> None:
    app = create_app(Settings(app_env="test", ml_service_token=SecretStr(TOKEN)))
    body = {"items": [{"product": "milk", "step": 500, "prior_mean": 1400, "observations": []}]}
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://ml"
    ) as c:
        ok = await c.post(
            "/pantry/quantities", json=body, headers={"Authorization": f"Bearer {TOKEN}"}
        )
        no = await c.post("/pantry/quantities", json=body)
    assert ok.status_code == 200 and ok.json()["items"][0]["mean"] == 1400.0
    assert ok.json()["model"] == "pantry-gamma-poisson-v0" and no.status_code == 401
