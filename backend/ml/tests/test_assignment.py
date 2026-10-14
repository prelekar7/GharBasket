"""Fair kitchen assignment (M10 §3.5): feasibility, the fairness term, the greedy baseline, the
endpoint."""

import random
from typing import Any

import httpx2
import pytest
from gharbasket_ml import assignment
from gharbasket_ml.main import create_app
from gharbasket_ml.schemas import AssignRequest, AssignResponse
from gharbasket_ml.settings import Settings
from pydantic import SecretStr, ValidationError

TOKEN = "ml-test-token"


def request(
    orders: int = 12,
    kitchens: int = 4,
    seed: int = 7,
    fairness_lambda: float = 300,
    capacity: int = 30,
    **over: Any,
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
                "max_portions": capacity,
                "skills": ["veg", "jain"] if k % 2 == 0 else ["veg"],
                "rating": round(4.0 + 0.2 * (k % 5), 1),
                "earnings_paise": 100_000 * k,  # kitchen 0 earned nothing so far
            }
            for k in range(kitchens)
        ],
        "fairness_lambda": fairness_lambda,
        "time_limit_s": 2.0,
        **over,
    }
    return AssignRequest.model_validate(body)


def earnings(answer: AssignResponse) -> dict[str, int]:
    assert answer.summary is not None
    return {e.kitchen_id: e.earnings_paise for e in answer.summary.earnings_paise}


def test_every_order_is_cooked_within_capacity_and_skills() -> None:
    req = request()

    answer = assignment.assign(req)

    assert answer.status == "solved" and answer.summary is not None and answer.unassigned == []
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
    assert list(earnings(answer)) == [k.id for k in req.kitchens]
    assert sum(earnings(answer).values()) == sum(k.earnings_paise for k in req.kitchens) + sum(
        o.portions * req.paise_per_portion for o in req.orders
    )


def test_fairness_lowers_the_gini_at_a_small_cost() -> None:
    cost_only = assignment.assign(request(fairness_lambda=0))
    fair = assignment.assign(request(fairness_lambda=1000))

    assert cost_only.summary is not None and fair.summary is not None
    assert fair.summary.gini < cost_only.summary.gini
    assert fair.summary.cost_paise_per_portion >= cost_only.summary.cost_paise_per_portion
    assert earnings(fair)["kitchen-0"] > earnings(cost_only)["kitchen-0"]  # the newcomer


def test_lambda_is_paise_per_portion_of_imbalance_and_does_not_saturate() -> None:
    """Deviations are counted in portions, so λ trades like for like with the cost per portion:
    each step up the sweep buys more fairness for more cost (it saturated at λ ≈ 0.2 when the
    deviation was in paise)."""
    plans = [assignment.assign(request(fairness_lambda=lam)).summary for lam in (0, 200, 1000)]

    assert all(p is not None for p in plans)
    ginis = [p.gini for p in plans if p is not None]
    costs = [p.cost_paise_per_portion for p in plans if p is not None]
    assert ginis[0] > ginis[1] > ginis[2]
    assert costs[0] <= costs[1] <= costs[2]


def test_kitchens_that_cannot_cook_tonight_do_not_pin_the_fairness_term() -> None:
    req = request(fairness_lambda=1000)
    idle = [
        {"id": "idle", "lat": 19.03, "lng": 73.07, "max_portions": 0, "skills": ["veg", "jain"]},
        {"id": "poor", "lat": 19.03, "lng": 73.07, "max_portions": 30, "rating": 3.0},
    ]
    more = AssignRequest.model_validate(
        {**req.model_dump(), "kitchens": [*req.model_dump()["kitchens"], *idle]}
    )

    fair, with_idle = assignment.assign(req), assignment.assign(more)

    assert fair.summary is not None and with_idle.summary is not None
    assert with_idle.assignments == fair.assignments
    assert with_idle.summary.gini_eligible == fair.summary.gini_eligible == fair.summary.gini
    eligible = [e for k, e in earnings(with_idle).items() if k not in {"idle", "poor"}]
    assert assignment.gini(eligible) == with_idle.summary.gini_eligible
    assert with_idle.summary.gini > with_idle.summary.gini_eligible  # all kitchens: idle at ₹0


def test_an_order_no_kitchen_can_take_is_left_out_not_the_hub() -> None:
    body = request().model_dump()
    body["orders"][0].update(lat=19.48)  # 50 km north of the kitchens
    body["orders"][1].update(skills=["halal"])  # no kitchen has the skill
    req = AssignRequest.model_validate(body)

    for answer in (assignment.assign(req), assignment.baseline(req)):
        assert answer.status == "partial" and answer.summary is not None
        assert [(u.order_id, u.reason) for u in answer.unassigned] == [
            ("order-0", "out_of_reach"),
            ("order-1", "no_skills"),
        ]
        assert {a.order_id for a in answer.assignments} == {o.id for o in req.orders[2:]}
        assert answer.summary.portions == sum(o.portions for o in req.orders[2:])
    assert assignment.assign(req).model == "assignment-cpsat-v0"


def test_demand_above_capacity_skips_the_fewest_portions() -> None:
    req = request(orders=6, kitchens=2, capacity=12)  # 31 portions asked, 24 to give

    answer = assignment.assign(req)

    assert answer.status == "partial" and answer.summary is not None
    assert {u.reason for u in answer.unassigned} == {"no_room"}
    assert 24 - 8 < answer.summary.portions <= 24  # an order is at most 8 portions
    load = {k.id: 0 for k in req.kitchens}
    for a in answer.assignments:
        load[a.kitchen_id] += a.portions
    assert all(v <= 12 for v in load.values())


def test_greedy_is_the_baseline_and_the_fallback() -> None:
    req = request(kitchens=3)
    base = assignment.baseline(req)
    assert base.status == "fallback" and base.model == "greedy-v0"
    assert {a.order_id for a in base.assignments} == {o.id for o in req.orders}
    nowhere = assignment.assign(request(max_km=0.01))  # no kitchen within reach
    assert nowhere.status == "infeasible" and nowhere.summary is None
    assert {u.reason for u in nowhere.unassigned} == {"out_of_reach"}
    tiny = assignment.assign(request(orders=3, max_orders_for_solver=1))  # too many for the solver
    assert (tiny.status, tiny.model) == ("fallback", "greedy-v0")


def test_numbers_too_big_for_int64_go_to_greedy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(assignment, "OBJECTIVE_LIMIT", 1000)
    answer = assignment.assign(request())
    assert (answer.status, answer.model) == ("fallback", "greedy-v0")


def test_rolling_earnings_are_bounded() -> None:
    body = request().model_dump()
    body["kitchens"][0]["earnings_paise"] = 10**19
    with pytest.raises(ValidationError):
        AssignRequest.model_validate(body)
    for k in body["kitchens"]:
        k["earnings_paise"] = 10**12  # the largest allowed, at the cheapest portion
    big = AssignRequest.model_validate({**body, "paise_per_portion": 1, "fairness_lambda": 10_000})
    assert assignment.assign(big).status == "solved"


def test_the_rating_floor_keeps_poor_kitchens_out() -> None:
    req = request(kitchens=3, rating_floor=4.1)  # kitchen-0 rates 4.0, the others 4.2 and 4.4
    answer = assignment.assign(req)
    assert answer.status != "infeasible" and answer.assignments
    assert all(a.kitchen_id != "kitchen-0" for a in answer.assignments)


def test_only_the_nearest_kitchens_are_candidates() -> None:
    req = request(orders=30, kitchens=8, capacity=200, max_kitchens_per_order=2, time_limit_s=0.5)
    pairs = assignment.candidates(req)
    per_order: dict[int, int] = {}
    for c in pairs:
        per_order[c.order] = per_order.get(c.order, 0) + 1
    assert max(per_order.values()) == 2 and len(per_order) == 30
    allowed = {(c.order, c.kitchen) for c in pairs}
    answer = assignment.assign(req)
    index = {k.id: i for i, k in enumerate(req.kitchens)}
    assert all(
        (int(a.order_id.split("-")[1]), index[a.kitchen_id]) in allowed for a in answer.assignments
    )


def test_deterministic_solves_repeat_exactly_when_the_limit_binds() -> None:
    req = request(orders=200, kitchens=30, capacity=60, time_limit_s=0.2)
    runs = [assignment.assign(req, deterministic=True) for _ in range(3)]
    assert runs[0].status == "solved"
    assert runs[1].assignments == runs[0].assignments == runs[2].assignments


def test_building_the_model_counts_against_the_time_limit() -> None:
    req = request(orders=300, kitchens=40, capacity=60, time_limit_s=0.5)
    answer = assignment.assign(req, deterministic=False)
    assert answer.status in {"solved", "fallback"} and answer.seconds < 0.5 + 0.4


@pytest.mark.anyio
async def test_the_endpoint_assigns_with_the_token() -> None:
    app = create_app(Settings(app_env="test", ml_service_token=SecretStr(TOKEN)))
    body = request(orders=4, kitchens=2).model_dump()
    auth = {"Authorization": f"Bearer {TOKEN}"}
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://ml"
    ) as c:
        ok = await c.post("/assign/kitchens", json=body, headers=auth)
        no = await c.post("/assign/kitchens", json=body)
        body["kitchens"][0]["earnings_paise"] = 10**19
        huge = await c.post("/assign/kitchens", json=body, headers=auth)
    assert ok.status_code == 200 and len(ok.json()["assignments"]) == 4
    assert ok.json()["model"] == "assignment-cpsat-v0" and ok.json()["unassigned"] == []
    assert ok.json()["summary"]["earnings_paise"][0]["kitchen_id"] == "kitchen-0"
    assert no.status_code == 401 and huge.status_code == 422
