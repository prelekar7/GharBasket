"""The pantry twin through the gateway (M10 §3.3): the ML service, the fallback's closed form,
the recorded prediction."""

import uuid
from datetime import date

import httpx2
import pytest
from gharbasket_ml import pantry as ml_pantry
from gharbasket_ml.schemas import PantryRequest as MlPantryRequest
from gharbasket_ml.schemas import RouteRequest as MlRouteRequest
from gharbasket_ml.settings import Settings as MlSettings
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.intel_gateway import api, fallback
from gharbasket.modules.intel_gateway.schemas import PantryItem, PantryObservation, PantryRequest
from gharbasket.modules.subscriptions import rules

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


CASES: list[list[tuple[float, str | None, float | None]]] = [
    [],
    [(2000, None, None)] * 6,
    [(2000, "ran_out", None), (1800, None, None)],
    [(1500, "wasted", 2500), (1500, "just_right", 2500), (1500, "ran_out", 2500)],
    [(1500, "ran_out", None)] * 10,
    [(0, "just_right", 900), (600, "wasted", 600)],
    [(0, "ran_out", 800), (1400, "ran_out", 1400)],
    [(6000, "ran_out", 6000)] * 8,  # far above the prior: the grid widens
    [(150, "wasted", 200)] * 8,  # far below it
]


@pytest.mark.parametrize("observations", CASES)
@pytest.mark.parametrize("prior_weeks", [0.25, 2.0, 20.0])
def test_the_fallback_gives_the_services_answer(
    observations: list[tuple[float, str | None, float | None]], prior_weeks: float
) -> None:
    """The fallback runs the service's own computation (pure arithmetic on both sides), so a
    customer gets the same quantity and interval whether or not the ML service answers."""
    req = PantryRequest(
        items=[
            PantryItem(
                product="milk",
                step=500,
                prior_mean=1400,
                prior_weeks=prior_weeks,
                observations=[
                    PantryObservation(qty=q, signal=s, delivered=d)  # pyright: ignore[reportArgumentType]
                    for q, s, d in observations
                ],
            )
        ]
    )
    ours = fallback.pantry_quantities(req)
    theirs = ml_pantry.quantities(MlPantryRequest.model_validate(req.model_dump(mode="json")))
    assert ours.source == "fallback" and ours.model == theirs.model
    assert [i.model_dump() for i in ours.items] == [i.model_dump() for i in theirs.items]


def test_the_fallbacks_constants_are_the_services() -> None:
    assert fallback.PANTRY_WEEK_SIGMA == ml_pantry.WEEK_SIGMA
    assert fallback.PANTRY_WASTED_SHARE == ml_pantry.WASTED_SHARE
    assert (fallback.PANTRY_GRID, fallback.PANTRY_SPAN) == (ml_pantry.GRID, ml_pantry.SPAN)
    assert fallback.PANTRY_MIN_PRIOR_WEEKS == ml_pantry.MIN_PRIOR_WEEKS
    assert fallback.PANTRY_BISECT == ml_pantry.BISECT
    assert (fallback.PANTRY_EDGE_MASS, fallback.PANTRY_MAX_LOG_SPAN) == (
        ml_pantry.EDGE_MASS,
        ml_pantry.MAX_LOG_SPAN,
    )
    assert PantryItem(product="m", step=1, prior_mean=1).prior_weeks == rules.RULE_PRIOR_WEEKS


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
    assert answer.source == "ml" and answer.model == "pantry-tap-censored-v0.2"
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


@pytest.mark.parametrize("failure", ["timeout", "server_error", "malformed", "not_an_object"])
async def test_any_ml_failure_falls_back_to_the_closed_form(failure: str) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if failure == "timeout":
            raise httpx2.ReadTimeout("slow", request=request)
        if failure == "server_error":
            return httpx2.Response(500, json={})
        if failure == "malformed":
            return httpx2.Response(200, json={"items": [{"product": "milk"}], "model": "x"})
        return httpx2.Response(200, json=[1, 2, 3])

    settings = make_settings(ml_service_url="http://ml.test", ml_service_token=TOKEN)
    req = request((2000, "ran_out"))
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        answer = await api.pantry_quantities(http, settings, req)

    assert answer == fallback.pantry_quantities(req)


# --- /route/solve through the gateway (M10 §3.4) ------------------------------------------------


def route_request(**overrides: object) -> api.RouteRequest:
    return api.RouteRequest.model_validate(
        {
            "hub": {"lat": 19.03, "lng": 73.069},
            "depart_s": 23400,
            "return_by_s": 41400,
            "stops": [
                {
                    "id": f"society-{i}",
                    "lat": 19.03 + i * 0.004,
                    "lng": 73.07 + i * 0.003,
                    "orders": 6,
                    "kg_tenths": 240,
                    "crates": 2,
                    "window_start_s": 25200,
                    "window_end_s": 32400,
                }
                for i in range(4)
            ],
            "vans": {"count": 2},
            "time_limit_s": 1.0,
            **overrides,
        }
    )


async def test_routes_come_from_the_service_and_are_recorded_for_the_hub(
    db_session: AsyncSession,
) -> None:
    settings = make_settings(ml_service_url="http://ml", ml_service_token=TOKEN)
    hub = uuid.uuid4()
    async with httpx2.AsyncClient(transport=ml_transport(), base_url="http://ml") as http:
        plan = await api.solve_routes(
            http,
            settings,
            route_request(),
            session=db_session,
            hub_id=hub,
            for_date=date(2026, 10, 13),
        )
    assert plan is not None and plan.status == "solved" and plan.source == "haversine"
    assert plan.summary.problems == [] and sum(len(r.stops) for r in plan.routes) == 4
    kept = await api.latest_prediction(
        db_session, kind="route", subject_id=hub, for_date=date(2026, 10, 13)
    )
    assert kept is not None and kept.model_name == "cvrptw-waves-v0"


async def test_without_the_service_there_are_no_routes() -> None:
    down = make_settings(ml_service_url="http://down.invalid", ml_timeout_s=0.5)
    async with httpx2.AsyncClient() as http:
        assert await api.solve_routes(http, down, route_request()) is None
    assert await api.solve_routes(httpx2.AsyncClient(), make_settings(), route_request()) is None


async def test_service_and_cost_reach_the_service_in_its_own_shape() -> None:
    request = route_request(
        service={"drop_s": 300, "per_crate_s": 20},
        cost={"fixed_paise_per_van": 30_000, "late_paise_per_s": 80},
        max_late_s=900,
    )
    # The ML service forbids unknown fields: the gateway's copy must match it field for field.
    assert MlRouteRequest.model_validate(request.model_dump(mode="json")).service.drop_s == 300
    settings = make_settings(ml_service_url="http://ml", ml_service_token=TOKEN)
    async with httpx2.AsyncClient(transport=ml_transport(), base_url="http://ml") as http:
        plan = await api.solve_routes(http, settings, request)
    assert plan is not None and plan.summary.problems == []


def test_the_gateway_refuses_what_the_service_would_refuse() -> None:
    with pytest.raises(ValidationError, match="donations need a food_bank"):
        route_request(
            stops=[
                {
                    "id": "s",
                    "lat": 19.03,
                    "lng": 73.07,
                    "donation_kg_tenths": 30,
                    "window_start_s": 25200,
                    "window_end_s": 32400,
                }
            ]
        )
    with pytest.raises(ValidationError, match="depart_s must be before return_by_s"):
        route_request(depart_s=41400)
    with pytest.raises(ValidationError, match="window_start_s is after window_end_s"):
        route_request(
            food_bank={"lat": 19.02, "lng": 73.04, "window_start_s": 43200, "window_end_s": 28800}
        )


def test_the_route_timeout_covers_the_osrm_wait_and_the_solver() -> None:
    settings = make_settings()
    request = route_request(time_limit_s=20.0)
    osrm = MlSettings(app_env="test").osrm_timeout_s
    assert api.route_timeout_s(settings, request) >= osrm + request.time_limit_s + 2.0
    assert api.route_timeout_s(settings, route_request(time_limit_s=0.5)) >= osrm + 0.5 + 2.0
