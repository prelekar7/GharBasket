"""The CP-SAT meal planner (M10 §3.1): valid plans, the comfort-rule fallback, and the checker."""

import time
from collections.abc import Callable
from typing import Any

import pytest
from gharbasket_ml.planner import (
    FALLBACK,
    Week,
    adherent_days,
    check,
    plan_week,
    relaxations,
    score,
    scored_days,
)
from gharbasket_ml.schemas import PlanRequest, PlanResponse, Weights

from .seed_weeks import request, v1_request

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


def with_body(req: PlanRequest, change: Callable[[dict[str, Any]], None]) -> PlanRequest:
    body = req.model_dump()
    change(body)
    return PlanRequest.model_validate(body)


def no_nutrition(body: dict[str, Any]) -> None:
    body["weights"] = Weights(nutrition=0).model_dump()


def test_v1_scores_nutrition_adherence_and_echoes_sides_and_reasons() -> None:
    req = v1_request(fasting_days=[{"day": 1, "no_onion_garlic": True}])

    response = plan_week(req)

    plan = verify(req, response)
    assert response.score is not None and response.score.nutrition is not None
    # Sunday is a no-cook day: its tiffins carry no nutrition, so only six days are scored.
    week = Week.of(req)
    assert scored_days(week) == [0, 1, 2, 3, 4, 5]
    days = adherent_days(week, plan)
    assert days is not None and sorted(days) == [0, 1, 2, 3, 4, 5]
    assert response.score.nutrition == round(sum(days.values()) / 6, 3)
    sides = {r.slug for r in req.recipes if r.is_side}
    assert not sides & set(plan.values())  # a side is never a meal
    by_slug = {r.slug: r for r in req.recipes}
    for m in response.plan or []:
        assert m.sides == (by_slug[m.recipe].sides if m.recipe else [])
    meals = {(m.day, m.slot): m for m in response.plan or []}
    assert "FESTIVAL" in meals[1, "lunch"].reasons and "TIFFIN" in meals[6, "dinner"].reasons
    v0 = plan_week(request())
    assert v0.score is not None and v0.score.nutrition is None  # a v0 request gets a v0 answer


def test_v1_nutrition_raises_adherence_over_an_ablation_without_it() -> None:
    req = v1_request()

    on = plan_week(req, deterministic=True)
    off = plan_week(with_body(req, no_nutrition), deterministic=True)

    assert on.score is not None and off.score is not None
    assert on.score.nutrition is not None and off.score.nutrition is not None
    assert on.score.nutrition > off.score.nutrition


def test_a_representative_v1_week_answers_well_inside_the_gateway_timeout() -> None:
    # The gateway waits 3 s (ml_timeout_s) and gives the solver at most half; the default limit
    # is 1 s for the whole request, so the answer leaves room for the network and the JSON.
    req = v1_request()
    assert req.time_limit_s == 1.0

    started = time.perf_counter()
    response = plan_week(req)
    elapsed = time.perf_counter() - started

    verify(req, response)
    assert response.status in ("optimal", "feasible")
    assert elapsed < 1.5, elapsed


def test_a_small_price_spike_does_not_erase_cuisine_preference() -> None:
    # Every preferred-cuisine dish 3 % above its index costs 0.03 x the price weight per meal,
    # far less than the cuisine match it brings (nutrition off to compare exact optima).
    req = with_body(v1_request(), no_nutrition)
    cuisines = set(req.household.cuisines)

    def spike(body: dict[str, Any]) -> None:
        for r in body["recipes"]:
            if r["cuisine"] in cuisines and not r["is_side"]:
                r["price_spike"] = 0.03

    calm = plan_week(req, deterministic=True)
    spiked = plan_week(with_body(req, spike), deterministic=True)

    assert calm.status == spiked.status == "optimal"
    assert calm.score is not None and spiked.score is not None
    assert spiked.score.preference == calm.score.preference > 0


def test_v1_drops_a_spiked_dish_for_an_unspiked_one() -> None:
    req = with_body(v1_request(), no_nutrition)
    calm = plan_week(req, deterministic=True)
    chosen = next(s for s in as_plan(calm).values() if s)

    def spike(body: dict[str, Any]) -> None:
        next(r for r in body["recipes"] if r["slug"] == chosen)["price_spike"] = 0.8

    spiked = plan_week(with_body(req, spike), deterministic=True)

    assert chosen in as_plan(calm).values() and chosen not in as_plan(spiked).values()
    assert spiked.score is not None and spiked.score.price_penalty is None
    assert calm.score is not None and calm.score.price_penalty is None


def test_v2_liking_tilts_the_plan_towards_kept_dishes_and_is_scored() -> None:
    body = v1_request().model_dump()
    mains = [r for r in body["recipes"] if not r["is_side"] and "dinner" in r["slots"]]
    assert len(mains) >= 2
    for r in body["recipes"]:
        r["preference"] = 0.0
    liked = mains[0]
    liked["preference"] = 1.0
    answer = plan_week(PlanRequest.model_validate(body), deterministic=True)
    assert answer.plan is not None and answer.score is not None
    chosen = [m.recipe for m in answer.plan if m.recipe]
    assert liked["slug"] in chosen and answer.score.liking is not None
    assert answer.score.liking == round(chosen.count(liked["slug"]) / len(chosen), 3)

    # With the liking weight off the term vanishes and the score still reports the mean.
    body["weights"] = {"liking": 0}
    off = plan_week(PlanRequest.model_validate(body), deterministic=True)
    assert off.plan is not None and off.score is not None and off.score.liking is not None
    # Unknown preferences: no liking in the score (v1 behaviour).
    plain = plan_week(v1_request(), deterministic=True)
    assert plain.score is not None and plain.score.liking is None


def test_v2_liking_tilts_the_plan_without_collapsing_variety() -> None:
    # Every main liked on a 0..1 ramp (as M3's rank-scaled preferences): v2 picks better-liked
    # dishes than v1, keeps every rule, and gives up at most one distinct dish of variety.
    v1 = v1_request()
    body = v1.model_dump()
    mains = sorted(r["slug"] for r in body["recipes"] if not r["is_side"])
    for r in body["recipes"]:
        if not r["is_side"]:
            r["preference"] = round(mains.index(r["slug"]) / (len(mains) - 1), 3)
    v2 = PlanRequest.model_validate(body)

    before = plan_week(v1, deterministic=True)
    after = plan_week(v2, deterministic=True)

    verify(v2, after)
    assert before.score is not None and after.score is not None
    liked_before = score(Week.of(v2), as_plan(before)).liking
    assert after.score.liking is not None and liked_before is not None
    assert after.score.liking > liked_before
    assert after.score.variety >= before.score.variety - 1
