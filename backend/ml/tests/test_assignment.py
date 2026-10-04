"""Fair kitchen assignment (M10 §3.5): feasibility, the fairness term, the greedy baseline, the
endpoint."""

import random

import httpx2
import pytest
from gharbasket_ml import assignment
from gharbasket_ml.main import create_app
from gharbasket_ml.schemas import AssignRequest
from gharbasket_ml.settings import Settings
from pydantic import SecretStr

TOKEN = "ml-test-token"


def request(
    orders: int = 12, kitchens: int = 4, seed: int = 7, fairness_lambda: float = 0.5, **over
) -> AssignRequest:
    rng = random.Random(seed)
    body = {
        "orders": [
            {
                "id": f"order-{i}",
                "lat": 19.03 + rng.uniform(-0.02, 0.02),
                "lng": 73.07 + rng.uniform(-0.02, 0.02),
                "portions": rng.randint(2, 8),
                "skills": ["veg"] if i % 3 else ["veg", "jain"],
            }
            for i in range(orders)
        ],
        "kitchens": [
            {
                "id": f"kitchen-{k}",
                "lat": 19.03 + rng.uniform(-0.02, 0.02),
                "lng": 73.07 + rng.uniform(-0.02, 0.02),
                "max_portions": 30,
                "skills": ["veg", "jain"] if k % 2 == 0 else ["veg"],
                "rating": 4.0 + 0.2 * k,
                "earnings_paise": 100_000 * k,  # kitchen 0 earned nothing so far
            }
            for k in range(kitchens)
        ],
        "fairness_lambda": fairness_lambda,
        "time_limit_s": 2.0,
        **over,
    }
    return AssignRequest.model_validate(body)


def test_every_order_is_cooked_within_capacity_and_skills() -> None:
    req = request()

    answer = assignment.assign(req)

    assert answer.status == "solved" and answer.summary is not None
    assert {a.order_id for a in answer.assignments} == {o.id for o in req.orders}
    load = {k.id: 0 for k in req.kitchens}
    skills = {k.id: set(k.skills) for k in req.kitchens}
    for a in answer.assignments:
        load[a.kitchen_id] += a.portions
        order = next(o for o in req.orders if o.id == a.order_id)
        assert set(order.skills) <= skills[a.kitchen_id] and a.km <= req.max_km
    assert all(load[k.id] <= k.max_portions for k in req.kitchens)
    assert answer.summary.portions == sum(o.portions for o in req.orders)
    assert 0 <= answer.summary.gini <= 1 and answer.seconds < 5


def test_fairness_lowers_the_gini_at_a_small_cost() -> None:
    cost_only = assignment.assign(request(fairness_lambda=0))
    fair = assignment.assign(request(fairness_lambda=5))

    assert cost_only.summary is not None and fair.summary is not None
    assert fair.summary.gini < cost_only.summary.gini
    assert fair.summary.cost_paise_per_portion >= cost_only.summary.cost_paise_per_portion
    assert fair.summary.earnings_paise[0] > cost_only.summary.earnings_paise[0]  # the newcomer


def test_greedy_is_the_baseline_and_the_fallback() -> None:
    req = request(kitchens=3)
    base = assignment.baseline(req)
    assert base.status == "fallback" and base.model == "greedy-v0"
    assert {a.order_id for a in base.assignments} == {o.id for o in req.orders}
    nowhere = request(max_km=0.01)  # no kitchen within reach
    assert assignment.assign(nowhere).status == "infeasible"
    tiny = request(orders=3, max_orders_for_solver=1)  # too many orders for the solver here
    assert assignment.assign(tiny).status == "fallback"


def test_the_rating_floor_keeps_poor_kitchens_out() -> None:
    req = request(kitchens=3, rating_floor=4.1)  # kitchen-0 rates 4.0, the others 4.2 and 4.4
    answer = assignment.assign(req)
    assert answer.status != "infeasible" and answer.assignments
    assert all(a.kitchen_id != "kitchen-0" for a in answer.assignments)


@pytest.mark.anyio
async def test_the_endpoint_assigns_with_the_token() -> None:
    app = create_app(Settings(app_env="test", ml_service_token=SecretStr(TOKEN)))
    body = request(orders=4, kitchens=2).model_dump()
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://ml"
    ) as c:
        ok = await c.post(
            "/assign/kitchens", json=body, headers={"Authorization": f"Bearer {TOKEN}"}
        )
        no = await c.post("/assign/kitchens", json=body)
    assert ok.status_code == 200 and len(ok.json()["assignments"]) == 4
    assert ok.json()["model"] == "assignment-cpsat-v0" and no.status_code == 401
