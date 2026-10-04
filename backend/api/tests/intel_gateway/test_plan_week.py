"""intel_gateway.plan_week (M10 §3.1): the ML service when it answers, the rules when it doesn't.

The ML service's own planner and checker are imported here (both packages live in the uv
workspace), so the rule-based fallback is held to exactly the same hard rules, and the gateway's
request shape is tested against the real service.
"""

import json
from pathlib import Path
from typing import Any

import httpx2
import pytest
import structlog
from gharbasket_ml import planner as ml_planner
from gharbasket_ml.main import create_app as create_ml_app
from gharbasket_ml.schemas import PlanRequest as MlPlanRequest
from gharbasket_ml.settings import Settings as MlSettings
from pydantic import SecretStr

from gharbasket.modules.intel_gateway import api, fallback
from gharbasket.modules.intel_gateway.schemas import PlanRequest, WeekPlan

from ..conftest import make_settings

pytestmark = pytest.mark.anyio

SEED = Path(__file__).resolve().parents[4] / "data" / "seeds" / "catalog"
BASE = {"g": 1, "kg": 1000, "ml": 1, "l": 1000, "piece": 1, "bunch": 1}
TOKEN = "gateway-test-token"


def seed_recipes() -> list[dict[str, Any]]:
    products = {p["slug"]: p for p in json.loads((SEED / "products.json").read_text())["products"]}
    prices = {
        i["slug"]: i["price_paise"] for i in json.loads((SEED / "prices.json").read_text())["items"]
    }
    out = []
    for r in json.loads((SEED / "recipes.json").read_text())["recipes"]:
        if r.get("is_side"):
            continue
        tags: set[str] = set()
        cost = 0.0
        for i in r["ingredients"]:
            p = products[i["product"]]
            tags |= set(p["tags"])
            cost += i["qty"] / (p["step"]["qty"] * BASE[p["step"]["unit"]]) * prices[i["product"]]
        no_og = "onion_garlic" not in tags
        diet = "egg" if "egg" in tags else "veg"
        out.append(
            {
                "slug": r["slug"],
                "cuisine": r["cuisine"],
                "slots": r["slots"],
                "cook_minutes": r["cook_minutes"],
                "diet": diet,
                "jain_ok": no_og and "root" not in tags and diet == "veg",
                "no_onion_garlic": no_og,
                "fasting_ok": r["fasting_ok"],
                "diabetic_ok": r["diabetic_ok"],
                "no_cook": "no_cook" in r["tags"],
                "cost_per_serving_paise": round(cost),
                "perishables": [
                    i["product"]
                    for i in r["ingredients"]
                    if products[i["product"]]["shelf_life_h"] <= 96
                ],
            }
        )
    return out


def plan_request(fasting_days: list[dict[str, Any]] | None = None, **household: Any) -> PlanRequest:
    return PlanRequest.model_validate(
        {
            "household": {
                "size": 4,
                "diet": "veg",
                "cuisines": ["maharashtrian", "north_indian"],
                "weekday_cook_minutes": 30,
                "weekend_cook_minutes": 60,
                "no_cook_days": [6],
                "weekly_budget_paise": 138568,
                **household,
            },
            "fasting_days": fasting_days or [],
            "recipes": seed_recipes(),
        }
    )


def ml_check(req: PlanRequest, answer: WeekPlan) -> list[str]:
    """The ML service's independent checker, on any plan."""
    assert answer.plan is not None
    week = ml_planner.Week.of(MlPlanRequest.model_validate(req.model_dump()))
    plan: ml_planner.Plan = {(m.day, m.slot): m.recipe for m in answer.plan}
    return ml_planner.check(week, plan, frozenset(answer.relaxed))


def ml_transport() -> httpx2.ASGITransport:
    return httpx2.ASGITransport(
        app=create_ml_app(MlSettings(app_env="test", ml_service_token=SecretStr(TOKEN)))
    )


# --- the fallback alone -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("fasting", "household", "relaxed"),
    [
        (None, {}, []),
        ([{"day": 1, "no_onion_garlic": True}], {}, []),
        (
            None,
            {
                "size": 2,
                "diabetic": True,
                "weekday_cook_minutes": 20,
                "weekend_cook_minutes": 30,
                "no_cook_days": [],
                "weekly_budget_paise": 92379,
            },
            [],  # recipe batch 1 added enough quick diabetic mains: no relaxation needed
        ),
        (
            [{"day": d, "no_onion_garlic": True} for d in range(7)],
            {
                "size": 3,
                "jain": True,
                "cuisines": ["gujarati"],
                "weekday_cook_minutes": 40,
                "no_cook_days": [],
                "weekly_budget_paise": 115473,
            },
            [],  # and enough Jain fasting dishes
        ),
    ],
    ids=["family", "tuesday-fast", "diabetic-quick", "jain-navratri"],
)
def test_the_fallback_keeps_every_hard_rule(
    fasting: list[dict[str, Any]] | None, household: dict[str, Any], relaxed: list[str]
) -> None:
    req = plan_request(fasting, **household)

    answer = fallback.plan_week(req)

    assert (answer.source, answer.status, answer.model) == ("fallback", "feasible", "rules-v0")
    assert answer.relaxed == relaxed
    assert ml_check(req, answer) == []


def test_the_fallback_says_infeasible_when_nothing_is_allowed() -> None:
    req = plan_request(jain=True)
    req = req.model_copy(update={"recipes": [r for r in req.recipes if not r.jain_ok]})

    answer = fallback.plan_week(req)

    assert (answer.status, answer.plan) == ("infeasible", None)


# --- the gateway --------------------------------------------------------------------------------


async def test_without_an_ml_service_the_rules_answer() -> None:
    async with httpx2.AsyncClient() as http:
        answer = await api.plan_week(http, make_settings(), plan_request())

    assert answer.source == "fallback"


async def test_the_ml_service_plans_when_it_answers() -> None:
    settings = make_settings(ml_service_url="http://ml.test", ml_service_token=TOKEN)
    req = plan_request()

    async with httpx2.AsyncClient(transport=ml_transport()) as http:
        answer = await api.plan_week(http, settings, req)

    assert (answer.source, answer.status, answer.model) == ("ml", "optimal", "cpsat-v0")
    assert ml_check(req, answer) == []


@pytest.mark.parametrize(
    "failure",
    ["timeout", "refused", "server_error", "wrong_token", "garbage"],
)
async def test_any_ml_failure_falls_back_to_the_rules(failure: str) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if failure == "timeout":
            raise httpx2.ReadTimeout("slow", request=request)
        if failure == "refused":
            raise httpx2.ConnectError("down", request=request)
        if failure == "garbage":
            return httpx2.Response(200, text="not json")
        return httpx2.Response(500 if failure == "server_error" else 401, json={})

    settings = make_settings(ml_service_url="http://ml.test", ml_service_token=TOKEN)
    req = plan_request()
    transport = ml_transport() if failure == "wrong_token" else httpx2.MockTransport(handler)
    if failure == "wrong_token":
        settings = make_settings(ml_service_url="http://ml.test", ml_service_token="nope")

    async with httpx2.AsyncClient(transport=transport) as http:
        answer = await api.plan_week(http, settings, req)

    assert answer.source == "fallback"
    assert ml_check(req, answer) == []


async def test_the_request_id_and_token_are_sent() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(500)

    settings = make_settings(ml_service_url="http://ml.test/", ml_service_token=TOKEN)
    structlog.contextvars.bind_contextvars(request_id="req-123")
    try:
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
            await api.plan_week(http, settings, plan_request())
    finally:
        structlog.contextvars.unbind_contextvars("request_id")

    assert str(seen[0].url) == "http://ml.test/plan/week"
    assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"
    assert seen[0].headers["x-request-id"] == "req-123"


def test_the_fallback_pairs_sides_and_never_plans_one_as_a_meal() -> None:
    req = PlanRequest.model_validate(
        {
            "household": {
                "size": 4,
                "diet": "veg",
                "cuisines": ["maharashtrian"],
                "weekday_cook_minutes": 30,
                "weekend_cook_minutes": 60,
                "no_cook_days": [6],
            },
            "recipes": [
                {
                    "slug": f"dish-{i}",
                    "cuisine": "maharashtrian",
                    "slots": ["breakfast", "lunch", "dinner"],
                    "cook_minutes": 20,
                    "diet": "veg",
                    "jain_ok": True,
                    "no_onion_garlic": True,
                    "fasting_ok": True,
                    "diabetic_ok": True,
                    "cost_per_serving_paise": 1000 + i,
                    "no_cook": True,  # Sunday breakfast needs one
                    "sides": ["chapati"],
                }
                for i in range(6)
            ]
            + [
                {
                    "slug": "chapati",
                    "cuisine": "common",
                    "slots": ["lunch", "dinner"],
                    "cook_minutes": 20,
                    "diet": "veg",
                    "jain_ok": True,
                    "no_onion_garlic": True,
                    "fasting_ok": False,
                    "diabetic_ok": True,
                    "cost_per_serving_paise": 300,
                    "is_side": True,
                }
            ],
        }
    )

    answer = fallback.plan_week(req)

    assert answer.plan is not None and answer.status == "feasible"
    meals = {(m.day, m.slot): m for m in answer.plan}
    assert all(m.recipe != "chapati" for m in answer.plan)
    assert meals[0, "lunch"].sides == ["chapati"] and "FALLBACK" in meals[0, "lunch"].reasons
    assert meals[6, "dinner"].recipe is None and "TIFFIN" in meals[6, "dinner"].reasons
