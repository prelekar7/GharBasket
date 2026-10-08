"""`intel_gateway.api.rank_basket`: the ML ranker when it answers, else frequency then
popularity; the rules planner prefers liked dishes (planner v2)."""

import uuid
from datetime import date

import httpx2
import pytest
from gharbasket_ml.schemas import RankRequest as MlRankRequest
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.intel_gateway import api as gateway
from gharbasket.modules.intel_gateway import fallback
from gharbasket.modules.intel_gateway.schemas import PlanRequest, RankCandidate, RankRequest

from ..conftest import make_settings
from .test_plan_week import TOKEN
from .test_plan_week import plan_request as sample_request

pytestmark = pytest.mark.anyio


def candidates() -> RankRequest:
    return RankRequest(
        candidates=[
            RankCandidate(product="milk", weeks_since=1, frequency=1.0, popularity=0.9),
            RankCandidate(product="bhindi", weeks_since=3, frequency=0.25, popularity=0.2),
            RankCandidate(product="paneer", weeks_since=1, frequency=0.5, popularity=0.95),
        ],
        top_k=3,
    )


async def test_without_an_ml_service_frequency_then_popularity_rank() -> None:
    async with httpx2.AsyncClient() as http:
        ranked = await gateway.rank_basket(http, make_settings(), candidates())
    assert ranked.source == "fallback" and ranked.model == fallback.RANKER_MODEL
    assert [r.product for r in ranked.ranked] == ["milk", "paneer", "bhindi"]
    assert [r.rank for r in ranked.ranked] == [1, 2, 3]


def test_the_request_is_one_the_ml_service_accepts() -> None:
    # The ML schema forbids extra fields: a field it dropped (drafted_rule, #119) would 422 every
    # call and silently fall back.
    sent = candidates().model_dump(mode="json")
    assert MlRankRequest.model_validate(sent).model_dump(mode="json") == sent
    with pytest.raises(ValueError):
        RankRequest(candidates=candidates().candidates, top_k=101)


@pytest.mark.parametrize("failure", ["timeout", "server_error"])
async def test_a_failing_ml_ranker_falls_back_to_frequency(failure: str) -> None:
    calls: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        calls.append(request.url.path)
        if failure == "timeout":
            raise httpx2.ReadTimeout("slow", request=request)
        return httpx2.Response(503, json={"title": "down"})

    settings = make_settings(ml_service_url="http://ml.test", ml_service_token=TOKEN)
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        ranked = await gateway.rank_basket(http, settings, candidates())
    assert calls and calls[0] == "/basket/rank"
    assert ranked.source == "fallback" and ranked.model == fallback.RANKER_MODEL
    assert [r.product for r in ranked.ranked] == ["milk", "paneer", "bhindi"]


@pytest.mark.services
async def test_the_ml_ranker_answers_and_is_recorded(db_session: AsyncSession) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/basket/rank"
        return httpx2.Response(
            200,
            json={
                "source": "ml",
                "model": "basket_ranker-20261004",
                "ranked": [
                    {"product": "bhindi", "score": 0.8, "rank": 1},
                    {"product": "milk", "score": 0.6, "rank": 2},
                ],
            },
        )

    household = uuid.uuid4()
    settings = make_settings(ml_service_url="http://ml", ml_service_token="t")
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        ranked = await gateway.rank_basket(
            http,
            settings,
            candidates(),
            session=db_session,
            household_id=household,
            week_monday=date(2026, 10, 12),
        )
    assert ranked.source == "ml" and ranked.ranked[0].product == "bhindi"
    kept = await gateway.latest_prediction(
        db_session, kind="basket_rank", subject_id=household, for_date=date(2026, 10, 12)
    )
    assert kept is not None and kept.model_name == "basket_ranker-20261004"
    assert kept.payload["ranked"][1]["product"] == "milk"


def test_the_rules_planner_prefers_liked_dishes_within_a_cuisine() -> None:
    request = sample_request()
    body = request.model_dump()
    for r in body["recipes"]:
        r["preference"] = 0.0
    liked = next(r for r in body["recipes"] if "lunch" in r["slots"] and not r["is_side"])
    liked["preference"] = 1.0
    plan = fallback.plan_week(PlanRequest.model_validate(body))
    assert plan.plan is not None
    chosen = [m.recipe for m in plan.plan if m.recipe]
    assert liked["slug"] in chosen


def test_a_budget_pass_ranks_by_cost_alone_and_keeps_the_budget() -> None:
    # Budget at the cheapest plan's cost, with the costliest allowed dish fully liked: the
    # cost-only pass must find the cheapest plan instead of relaxing the budget.
    body = sample_request().model_dump()
    plain = PlanRequest.model_validate(body)
    cheapest = fallback._greedy(plain, frozenset(), by_cost=True)  # pyright: ignore[reportPrivateUsage]
    assert cheapest is not None
    costs = {r["slug"]: r["cost_per_serving_paise"] for r in body["recipes"]}
    size = body["household"]["size"]
    budget = sum(costs[s] * size for s in cheapest.values() if s)
    lunch = [r for r in body["recipes"] if "lunch" in r["slots"] and not r["is_side"]]
    dear = max(lunch, key=lambda r: r["cost_per_serving_paise"])
    assert dear["slug"] not in cheapest.values()
    dear["preference"] = 1.0
    body["household"]["weekly_budget_paise"] = budget

    plan = fallback.plan_week(PlanRequest.model_validate(body))

    assert plan.plan is not None and plan.relaxed == []
    chosen = [m.recipe for m in plan.plan if m.recipe]
    assert sum(costs[s] * size for s in chosen) <= budget
