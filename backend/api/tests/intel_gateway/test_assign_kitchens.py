"""Kitchen assignment through the gateway (M10 §3.5): the ML service's plan, partial plans, the
recorded prediction, and None whenever the service does not answer (M8 then assigns greedily)."""

import uuid
from datetime import date
from typing import Any

import httpx2
import pytest
from gharbasket_ml.schemas import AssignRequest as MlAssignRequest
from gharbasket_ml.schemas import AssignResponse as MlAssignResponse
from gharbasket_ml.schemas import KitchenIn, TiffinOrderIn
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.intel_gateway import api
from gharbasket.modules.intel_gateway.schemas import Kitchen, KitchenAssignment, TiffinOrder

from ..conftest import make_settings
from .test_plan_week import TOKEN, ml_transport

pytestmark = pytest.mark.anyio


def body(**over: Any) -> dict[str, Any]:
    return {
        "orders": [
            {"id": "o1", "lat": 19.031, "lng": 73.071, "portions": 4, "skills": ["veg"]},
            {"id": "o2", "lat": 19.034, "lng": 73.074, "portions": 6},
        ],
        "kitchens": [
            {"id": "k1", "lat": 19.032, "lng": 73.072, "max_portions": 10, "skills": ["veg"]},
            {"id": "k2", "lat": 19.040, "lng": 73.080, "max_portions": 10, "skills": ["veg"]},
        ],
        "time_limit_s": 1.0,
        **over,
    }


def request(**over: Any) -> api.AssignRequest:
    return api.AssignRequest.model_validate(body(**over))


def fields(model: type[BaseModel]) -> dict[str, Any]:
    return {name: (f.annotation, f.default, f.metadata) for name, f in model.model_fields.items()}


def test_the_gateway_mirrors_the_service_contract() -> None:
    ml = fields(MlAssignRequest)
    ml.pop("max_orders_for_solver")  # the service's own tuning, not part of M10 §3.5
    mine = fields(api.AssignRequest)
    assert mine.keys() == ml.keys()
    assert all(mine[k][1:] == ml[k][1:] for k in mine), "defaults and bounds differ"
    assert fields(Kitchen) == fields(KitchenIn) and fields(TiffinOrder) == fields(TiffinOrderIn)
    assert KitchenAssignment.model_fields.keys() == MlAssignResponse.model_fields.keys()
    with pytest.raises(ValidationError):
        request(kitchens=[{**body()["kitchens"][0], "earnings_paise": 10**19}])
    with pytest.raises(ValidationError):
        request(quality_paise_per_star=-1)


async def test_assignments_come_from_the_service_and_are_recorded(db_session: AsyncSession) -> None:
    settings = make_settings(ml_service_url="http://ml", ml_service_token=TOKEN)
    hub = uuid.uuid4()
    async with httpx2.AsyncClient(transport=ml_transport(), base_url="http://ml") as http:
        plan = await api.assign_kitchens(
            http, settings, request(), session=db_session, hub_id=hub, for_date=date(2026, 10, 13)
        )
    assert plan is not None and plan.status == "solved" and len(plan.assignments) == 2
    assert plan.summary is not None and plan.summary.portions == 10 and plan.unassigned == []
    assert [e.kitchen_id for e in plan.summary.earnings_paise] == ["k1", "k2"]
    kept = await api.latest_prediction(
        db_session, kind="assignment", subject_id=hub, for_date=date(2026, 10, 13)
    )
    assert kept is not None and kept.model_name == "assignment-cpsat-v0"


async def test_a_partial_plan_names_the_orders_left_out() -> None:
    settings = make_settings(ml_service_url="http://ml", ml_service_token=TOKEN)
    orders = body()["orders"]
    orders[1] = {**orders[1], "skills": ["halal"]}
    async with httpx2.AsyncClient(transport=ml_transport(), base_url="http://ml") as http:
        plan = await api.assign_kitchens(http, settings, request(orders=orders))
    assert plan is not None and plan.status == "partial"
    assert [(a.order_id, a.kitchen_id) for a in plan.assignments] == [("o1", "k1")]
    assert [(u.order_id, u.reason) for u in plan.unassigned] == [("o2", "no_skills")]


@pytest.mark.parametrize("failure", ["timeout", "refused", "server_error", "garbage"])
async def test_any_failure_returns_none(failure: str) -> None:
    def handler(req: httpx2.Request) -> httpx2.Response:
        if failure == "timeout":
            raise httpx2.ReadTimeout("slow", request=req)
        if failure == "refused":
            raise httpx2.ConnectError("down", request=req)
        if failure == "garbage":
            return httpx2.Response(200, json={"status": "solved"})
        return httpx2.Response(500, json={})

    settings = make_settings(ml_service_url="http://ml", ml_service_token=TOKEN)
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        assert await api.assign_kitchens(http, settings, request()) is None


async def test_a_wrong_token_or_no_service_returns_none() -> None:
    wrong = make_settings(ml_service_url="http://ml", ml_service_token="nope")
    async with httpx2.AsyncClient(transport=ml_transport(), base_url="http://ml") as http:
        assert await api.assign_kitchens(http, wrong, request()) is None
        assert await api.assign_kitchens(http, make_settings(), request()) is None


async def test_the_timeout_covers_the_solver_limit_plus_a_margin() -> None:
    seen: list[httpx2.Request] = []

    def handler(req: httpx2.Request) -> httpx2.Response:
        seen.append(req)
        return httpx2.Response(500)

    settings = make_settings(ml_service_url="http://ml", ml_service_token=TOKEN, ml_timeout_s=8)
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        await api.assign_kitchens(http, settings, request(time_limit_s=20.0))
        await api.assign_kitchens(http, settings, request(time_limit_s=0.5))
    waits = [r.extensions["timeout"]["read"] for r in seen]
    assert waits == [20.0 + api.ASSIGN_MARGIN_S, 8]  # never below the usual timeout
    assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"
