"""The CP-SAT meal planner (M10 §3.1): valid plans, the comfort-rule fallback, and the checker."""

from typing import Any

import pytest
from gharbasket_ml.planner import FALLBACK, Week, check, plan_week, relaxations
from gharbasket_ml.schemas import PlanRequest, PlanResponse

from .seed_weeks import request

NAVRATRI = [{"day": d, "no_onion_garlic": True} for d in range(7)]


def as_plan(response: PlanResponse) -> dict:
    assert response.plan is not None
    return {(m.day, m.slot): m.recipe for m in response.plan}


def verify(req: PlanRequest, response: PlanResponse) -> dict:
    plan = as_plan(response)
    assert check(Week.of(req), plan, frozenset(response.relaxed)) == []
    return plan


def test_a_family_week_is_optimal_and_valid() -> None:
    req = request()

    response = plan_week(req)

    assert (response.status, response.relaxed) == ("optimal", [])
    plan = verify(req, response)
    assert len(plan) == 21
    assert plan[6, "lunch"] is None and plan[6, "dinner"] is None  # Sunday: tiffin
    assert response.score is not None
    assert response.score.cost_paise <= 138568
    assert response.score.variety >= 12 and response.score.perishables <= 8
    assert response.seconds < 2


def test_deterministic_mode_gives_the_same_optimal_plan_every_time() -> None:
    req = request(
        size=2, diet="egg", cuisines=["maharashtrian"], no_cook_days=[], weekly_budget_paise=None
    )

    plans = [plan_week(req, deterministic=True) for _ in range(3)]

    assert {r.status for r in plans} == {"optimal"}
    assert verify(req, plans[0]) == as_plan(plans[1]) == as_plan(plans[2])


def test_an_egg_household_may_get_egg_curry_and_a_veg_one_never() -> None:
    egg = plan_week(request(diet="egg", no_cook_days=[], weekly_budget_paise=None))
    veg = plan_week(request())

    assert egg.status == "optimal"
    assert "egg-curry" not in as_plan(veg).values()


def test_a_tuesday_fast_gets_a_fasting_dish_and_no_onion_or_garlic() -> None:
    req = request(fasting_days=[{"day": 1, "no_onion_garlic": True}])

    response = plan_week(req)

    plan = verify(req, response)
    tuesday = [
        req_r
        for req_r in req.recipes
        if req_r.slug in {plan[1, s] for s in ("breakfast", "lunch", "dinner")}
    ]
    assert any(r.fasting_ok for r in tuesday)
    assert all(r.no_onion_garlic for r in tuesday)


def test_a_diabetic_household_with_quick_meals_needs_no_relaxation() -> None:
    # Ramesh (seed): with 20-minute weekday meals the seed menu is too small for 3-day windows.
    req = request(
        size=2,
        diabetic=True,
        weekday_cook_minutes=20,
        weekend_cook_minutes=30,
        no_cook_days=[],
        weekly_budget_paise=92379,
    )

    response = plan_week(req)

    assert response.relaxed == []  # recipe batch 1 (F7) made this feasible without relaxing
    plan = verify(req, response)
    by_slug = {r.slug: r for r in req.recipes}
    assert all(by_slug[s].diabetic_ok for (_, slot), s in plan.items() if s and slot != "breakfast")


def test_a_jain_household_in_navratri_keeps_every_religious_rule() -> None:
    # Meena (seed): Jain, fasting every day of Navratri.
    req = request(
        fasting_days=NAVRATRI,
        size=3,
        jain=True,
        cuisines=["gujarati"],
        weekday_cook_minutes=40,
        weekend_cook_minutes=60,
        no_cook_days=[],
        weekly_budget_paise=115473,
    )

    response = plan_week(req)

    assert response.relaxed == []  # recipe batch 1 (F7) made this feasible without relaxing
    plan = verify(req, response)
    by_slug = {r.slug: r for r in req.recipes}
    assert all(by_slug[s].jain_ok for s in plan.values() if s)
    for day in range(7):
        assert any(by_slug[plan[day, s]].fasting_ok for s in ("breakfast", "lunch", "dinner"))


def test_with_no_allowed_dishes_the_answer_is_infeasible() -> None:
    req = request(jain=True)
    req = req.model_copy(update={"recipes": [r for r in req.recipes if not r.jain_ok]})

    response = plan_week(req)

    assert (response.status, response.plan, response.score) == ("infeasible", None, None)


def test_comfort_rules_are_relaxed_in_order_never_safety_rules() -> None:
    assert FALLBACK == ("repeat", "cook_time", "budget")
    assert [sorted(r) for r in relaxations()] == [
        [],
        ["repeat"],
        ["cook_time"],
        ["budget"],
        ["cook_time", "repeat"],
        ["budget", "cook_time"],
    ]


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (((0, "lunch"), "egg-curry"), "breaks a rule"),  # a veg household
        (((0, "lunch"), None), "tiffin on a cooking day"),
        (((1, "lunch"), "PLACEHOLDER"), "repeats within"),  # Monday's lunch again on Tuesday
    ],
)
def test_the_checker_catches_broken_plans(change: tuple, problem: str) -> None:
    req = request()
    plan = as_plan(plan_week(req))
    key, value = change
    plan[key] = plan[0, "lunch"] if value == "PLACEHOLDER" else value

    problems = check(Week.of(req), plan)

    assert any(problem in p for p in problems), problems


def test_the_checker_catches_a_missing_slot_and_the_budget() -> None:
    req = request(weekly_budget_paise=1000)
    plan = as_plan(plan_week(request()))
    del plan[3, "dinner"]

    problems = check(Week.of(req), plan)

    assert any("nothing planned" in p for p in problems)
    assert any("over the budget" in p for p in problems)


# --- v1 (M10 §3.2): nutrition, sides, price spikes ---------------------------------------------


def v1_request(**overrides: Any) -> PlanRequest:
    """A v1 request: every recipe gets a nutrition row (a plain dal-and-roti day lands near the
    adult target), sides are listed, and one dish is spiked."""
    body = request(**overrides).model_dump()
    for r in body["recipes"]:
        r["nutrition_per_serving"] = {"kcal": 520, "protein_g": 14, "fibre_g": 5}
        r["sides"] = ["chapati"] if "lunch" in r["slots"] else []
    body["recipes"].append(
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
            "nutrition_per_serving": {"kcal": 120, "protein_g": 4},
            "is_side": True,
        }
    )
    body["household"]["members"] = [
        {"age_band": "18_59", "sex": "f"},
        {"age_band": "18_59", "sex": "m"},
        {"age_band": "5_17", "sex": "f"},
        {"age_band": "under_5", "sex": "m"},
    ]
    return PlanRequest.model_validate(body)


def test_v1_scores_nutrition_adherence_and_echoes_sides_and_reasons() -> None:
    req = v1_request(no_cook_days=[6], fasting_days=[{"day": 1, "no_onion_garlic": True}])

    response = plan_week(req)

    plan = verify(req, response)
    assert response.score is not None and response.score.nutrition is not None
    assert 0 <= response.score.nutrition <= 1
    assert "chapati" not in plan.values()  # a side is never a meal
    meals = {(m.day, m.slot): m for m in response.plan or []}
    lunch = meals[0, "lunch"]
    assert lunch.recipe and lunch.sides == ["chapati"]
    assert "FESTIVAL" in meals[1, "lunch"].reasons and "TIFFIN" in meals[6, "dinner"].reasons
    v0 = plan_week(request())
    assert v0.score is not None and v0.score.nutrition is None  # a v0 request gets a v0 answer


def test_v1_avoids_a_spiked_dish_when_another_is_as_good() -> None:
    req = v1_request()
    body = req.model_dump()
    spiked = next(r for r in body["recipes"] if r["slug"] == "bhindi-masala")
    spiked["price_spike"] = 0.8  # bhindi is 80 % above its 4-week index
    req = PlanRequest.model_validate(body)

    with_spike = plan_week(req, deterministic=True)
    without = plan_week(v1_request(), deterministic=True)

    assert with_spike.score is not None and without.score is not None
    assert (with_spike.score.price_penalty or 0) <= 0.8
    assert (
        "bhindi-masala" not in as_plan(with_spike).values()
        or "bhindi-masala" in as_plan(without).values()
    )
    assert without.score.price_penalty is None
