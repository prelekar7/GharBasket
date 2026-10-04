import dataclasses
from collections import Counter
from datetime import date
from typing import Any

from gharbasket_ml import planner as cpsat
from gharbasket_ml.schemas import PlanRequest
from gharbasket_sim import config
from gharbasket_sim.catalog import Catalog
from gharbasket_sim.households import SimHousehold, sample
from gharbasket_sim.weeks import (
    Planned,
    cached,
    cpsat_planner,
    fasting_days,
    rules_planner,
    simulate,
)

NAVRATRI_WEEK = date(2026, 10, 12)  # Monday; Navratri runs 11-19 Oct in the seed calendar


def households(catalog: Catalog) -> list[SimHousehold]:
    return sample(7, config.load(), catalog)


def pick(catalog: Catalog, **rules: Any) -> SimHousehold:
    return next(h for h in households(catalog) if all(h.rules[k] == v for k, v in rules.items()))


def test_fasts_come_from_tuesdays_and_the_festival_calendar(catalog: Catalog) -> None:
    h = dataclasses.replace(
        households(catalog)[0], fasts=("navratri", "tuesdays"), no_onion_garlic_on_fasts=False
    )

    days = fasting_days(h, NAVRATRI_WEEK, catalog.festivals)

    # Navratri's own rule (no onion or garlic) applies on its days, Tuesday included.
    assert days == [{"day": d, "no_onion_garlic": True} for d in range(7)]
    tuesday_only = dataclasses.replace(h, fasts=("tuesdays",))
    assert fasting_days(tuesday_only, NAVRATRI_WEEK, catalog.festivals) == [
        {"day": 1, "no_onion_garlic": False}
    ]


def check_week(h: SimHousehold, catalog: Catalog, monday: date, rows: Any) -> None:
    """Every dish the household ends up cooking keeps the planner's hard rules."""
    week_row = rows.weeks[0]
    request = PlanRequest.model_validate(
        {
            "household": h.rules,
            "fasting_days": fasting_days(h, monday, catalog.festivals),
            "recipes": catalog.recipes,
        }
    )
    week = cpsat.Week.of(request)
    relax = frozenset(week_row["relaxed"])
    for m in rows.meals:
        day = (m["date"] - monday).days
        if m["final"] is not None:
            assert cpsat.allowed(week, week.recipes[m["final"]], day, m["slot"], relax), m
    for day in week.fasting:
        today = [m["final"] for m in rows.meals if (m["date"] - monday).days == day]
        assert any(s and week.recipes[s].fasting_ok for s in today)


def test_a_jain_household_in_navratri_eats_by_its_rules(catalog: Catalog) -> None:
    h = dataclasses.replace(pick(catalog, jain=True), fasts=("navratri",))

    rows = simulate(7, h, catalog, NAVRATRI_WEEK, 1, cpsat_planner)

    check_week(h, catalog, NAVRATRI_WEEK, rows)
    assert {m["action"] for m in rows.meals} <= {"accept", "edit", "tiffin"}
    banned = {"root", "onion_garlic"}
    eaten = {c["product"] for c in rows.consumption}
    assert not any(catalog.products[p].tags & banned for p in eaten)


def test_the_same_seed_gives_the_same_weeks(catalog: Catalog) -> None:
    h = households(catalog)[3]

    first = simulate(7, h, catalog, NAVRATRI_WEEK, 2, cpsat_planner)
    again = simulate(7, h, catalog, NAVRATRI_WEEK, 2, cpsat_planner)
    other = simulate(8, h, catalog, NAVRATRI_WEEK, 2, cpsat_planner)

    assert first == again
    assert first.meals != other.meals


def test_keen_households_accept_and_reluctant_ones_edit(catalog: Catalog) -> None:
    h = pick(catalog, diet="veg", jain=False)
    keen = dataclasses.replace(h, accept_bias=20.0, skip_week=0.0)
    reluctant = dataclasses.replace(h, accept_bias=-20.0, skip_week=1.0)

    yes = simulate(7, keen, catalog, NAVRATRI_WEEK, 1, rules_planner)
    no = simulate(7, reluctant, catalog, NAVRATRI_WEEK, 1, rules_planner)

    assert Counter(m["action"] for m in yes.meals)["edit"] == 0
    assert Counter(m["action"] for m in no.meals)["edit"] > 10
    assert yes.weeks[0]["ordered"] and not no.weeks[0]["ordered"]
    assert yes.weeks[0]["planner"] == "rules-v0"
    check_week(reluctant, catalog, NAVRATRI_WEEK, no)


def test_consumption_is_the_cooked_dishes_plus_daily_use(catalog: Catalog) -> None:
    h = dataclasses.replace(pick(catalog, diet="veg"), accept_bias=20.0)

    rows = simulate(7, h, catalog, NAVRATRI_WEEK, 1, rules_planner)

    eaten = {c["product"]: c for c in rows.consumption}
    assert set(h.daily_use) <= set(eaten)
    cooked = {i.product for m in rows.meals if m["final"] for i in catalog.ingredients[m["final"]]}
    assert cooked - set(eaten) <= {
        i.product for ings in catalog.ingredients.values() for i in ings if i.optional
    }
    assert all(c["unit"] == catalog.products[p].unit for p, c in eaten.items())
    assert all(c["qty"] > 0 for c in rows.consumption)


def test_without_a_plan_the_household_cooks_for_itself(catalog: Catalog) -> None:
    def nothing(request: dict[str, Any]) -> Planned:
        return Planned("infeasible", (), {}, "none")

    h = pick(catalog, diet="veg", jain=False)
    rows = simulate(7, h, catalog, NAVRATRI_WEEK, 1, nothing)

    assert rows.weeks[0]["plan_status"] == "infeasible"
    assert {m["action"] for m in rows.meals} <= {"own", "none"}
    assert Counter(m["action"] for m in rows.meals)["own"] >= 14


def test_identical_requests_are_planned_once() -> None:
    calls: list[dict[str, Any]] = []

    def counting(request: dict[str, Any]) -> Planned:
        calls.append(request)
        return Planned("optimal", (), {}, "count")

    plan = cached(counting)
    first = plan({"household": {"size": 2}, "recipes": []})
    second = plan({"recipes": [], "household": {"size": 2}})
    plan({"household": {"size": 3}, "recipes": []})

    assert first is second and len(calls) == 2
