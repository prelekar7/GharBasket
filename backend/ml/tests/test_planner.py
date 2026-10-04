"""The CP-SAT meal planner (M10 §3.1): valid plans, the comfort-rule fallback, and the checker."""

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


def test_a_diabetic_household_with_quick_meals_relaxes_only_repeats() -> None:
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

    assert response.relaxed == ["repeat"]
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

    assert response.relaxed == ["repeat"]
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
