"""The pure parts of cycle generation: fasting days, the rule table, plan → lines."""

import uuid
from datetime import date
from typing import Any

from gharbasket.modules.catalog.api import PlanCatalog, PlanIngredient, PlanProduct, PlanRecipe
from gharbasket.modules.identity.api import HouseholdPlanProfile
from gharbasket.modules.subscriptions import planning, rules
from gharbasket.modules.subscriptions.rules import Member

MONDAY = date(2026, 10, 12)


def profile(**overrides: object) -> HouseholdPlanProfile:
    base: dict[str, Any] = dict(
        id=uuid.uuid4(),
        owner_user_id=uuid.uuid4(),
        society_id=uuid.uuid4(),
        hub_id=uuid.uuid4(),
        size=4,
        diet="veg",
        jain=False,
        diabetic=False,
        cuisines=("maharashtrian",),
        weekday_cook_minutes=30,
        weekend_cook_minutes=60,
        no_cook_days=(6,),
        budget_paise=600_000,
        fasts=frozenset({"tuesdays", "navratri"}),
        no_onion_garlic_on_fasts=False,
        member_age_bands=("18_59", "18_59", "5_17", "under_5"),
        autopilot_enabled=True,
        auto_approve=False,
    )
    base.update(overrides)
    return HouseholdPlanProfile(**base)  # type: ignore[arg-type]


def product(
    slug: str,
    kind: str = "veg",
    step=(250, "g"),
    price: int | None = 2000,
    *,
    shelf_life_h: int = 72,
    nutrition: dict[str, Any] | None = None,
) -> PlanProduct:
    return PlanProduct(
        uuid.uuid5(uuid.NAMESPACE_URL, slug),
        slug,
        kind,
        {"en": slug},
        step[0],
        step[1],
        frozenset(),
        shelf_life_h,
        price,
        nutrition,
    )


def catalog() -> PlanCatalog:
    products = {
        p.id: p
        for p in (
            product("tomato"),
            product("toor-dal", "staple", (500, "g"), 7000),
            product("chapati-atta", "staple", (1, "kg"), 5000),
            product("milk", "staple", (500, "ml"), 3000),
            product("wheat-atta", "staple", (1, "kg"), 5000, shelf_life_h=2160),
            product("potato", "veg", (1, "kg"), 3000, shelf_life_h=720),
            product("banana", "fruit", (6, "piece"), 4000, nutrition={"unit_g": 120}),
            product("apple", "fruit", (500, "g"), 9000),
            product("guava", "fruit", (500, "g"), 3000),
            product("unpriced", "veg", (250, "g"), None),
        )
    }
    by = {p.slug: p for p in products.values()}
    dal = PlanRecipe(
        uuid.uuid4(),
        "dal-tadka",
        {"en": "Dal"},
        "north_indian",
        ("lunch", "dinner"),
        30,
        4,
        "veg",
        {"jain_ok": False, "no_onion_garlic": False, "fasting_ok": False, "diabetic_ok": True},
        ("dal",),
        False,
        ("chapati",),
        (
            PlanIngredient(by["toor-dal"].id, "toor-dal", 40, False),
            PlanIngredient(by["unpriced"].id, "unpriced", 10, True),
        ),
        1200,
        (),
    )
    chapati = PlanRecipe(
        uuid.uuid4(),
        "chapati",
        {"en": "Chapati"},
        "common",
        ("lunch", "dinner"),
        20,
        4,
        "veg",
        {"jain_ok": True, "no_onion_garlic": True, "fasting_ok": False, "diabetic_ok": True},
        ("roti",),
        True,
        (),
        (PlanIngredient(by["chapati-atta"].id, "chapati-atta", 60, False),),
        300,
        (),
    )
    sabzi = PlanRecipe(
        uuid.uuid4(),
        "tomato-sabzi",
        {"en": "Tomato sabzi"},
        "maharashtrian",
        ("lunch", "dinner"),
        25,
        2,
        "veg",
        {"jain_ok": False, "no_onion_garlic": False, "fasting_ok": False, "diabetic_ok": True},
        ("sabzi",),
        False,
        ("chapati",),
        (PlanIngredient(by["tomato"].id, "tomato", 100, False),),
        800,
        ("tomato",),
    )
    return PlanCatalog(products, (dal, chapati, sabzi))


def test_fasting_days_come_from_the_household_and_the_calendar() -> None:
    festivals = [(date(2026, 10, 14), "navratri", {"fasting": True, "no_onion_garlic": True})]

    days = planning.fasting_days(profile(), MONDAY, festivals)

    assert days == [{"day": 1, "no_onion_garlic": False}, {"day": 2, "no_onion_garlic": True}]
    assert planning.fasting_days(profile(fasts=frozenset()), MONDAY, festivals) == []
    shravan = planning.fasting_days(
        profile(fasts=frozenset({"shravan_mondays"})), date(2026, 8, 3), []
    )
    assert shravan == [{"day": 0, "no_onion_garlic": False}]


def test_the_plan_request_carries_rules_recipes_and_a_weekly_budget() -> None:
    request = planning.plan_request(profile(), catalog(), MONDAY, [])

    assert request["household"]["weekly_budget_paise"] == 600_000 * 7 // 30
    assert request["household"]["no_cook_days"] == [6]
    slugs = [r["slug"] for r in request["recipes"]]
    assert slugs == ["dal-tadka", "tomato-sabzi"]  # chapati is a side, not a meal
    assert request["recipes"][0]["cost_per_serving_paise"] == 1200


def test_the_rule_table_scales_with_adult_equivalents() -> None:
    family = rules.adult_equivalents(
        [Member("18_59"), Member("18_59"), Member("5_17"), Member("under_5")]
    )
    assert family == 3.2
    assert rules.daily_use_plan(family)["milk"] == 1400 * 3.2
    assert rules.steps_needed(1400 * 3.2, 500, 1) == 9  # 4480 ml in 500 ml steps
    assert rules.steps_needed(10, 500, 1) == 1 and rules.steps_needed(10, 0, 1) == 0


def test_lines_follow_the_plan_with_sides_and_daily_use_and_skip_unpriced() -> None:
    cat = catalog()
    plan = [
        {"day": 0, "slot": "lunch", "recipe": "dal-tadka"},
        {"day": 0, "slot": "dinner", "recipe": "tomato-sabzi"},
        {"day": 1, "slot": "lunch", "recipe": "tomato-sabzi"},
        {"day": 6, "slot": "lunch", "recipe": None},
    ]

    lines = {
        cat.products[line.product_id].slug: line
        for line in planning.lines_from_plan(profile(), cat, plan, fallback=False)
    }

    # Dal: 40 g a serving x 4 people ÷ 4 servings = 40 g a meal → 1 step of 500 g.
    assert lines["toor-dal"].qty == 1 and lines["toor-dal"].reason_code == "meal_plan"
    assert (
        lines["toor-dal"].reason_params == {"recipes": ["dal-tadka"]}
        and lines["toor-dal"].source == "ai"
    )
    # Tomato: 100 g a serving x 4 ÷ 2 servings = 200 g a meal, twice → 400 g → 2 steps of 250 g.
    assert lines["tomato"].qty == 2 and lines["tomato"].reason_params == {
        "recipes": ["tomato-sabzi"]
    }
    # Chapati is a side of both mains, three meals: 60 g x 4 ÷ 4 x 3 = 180 g → 1 kg step.
    assert lines["chapati-atta"].qty == 1
    assert "unpriced" not in lines  # optional and unpriced
    assert (
        lines["milk"].reason_code == "daily_use"
        and lines["milk"].source == "rule"
        and lines["milk"].qty == 9
    )
    fruits = [slug for slug, line in lines.items() if cat.products[line.product_id].kind == "fruit"]
    assert fruits == ["banana", "guava"]  # the two cheapest priced fruits
    assert sum(line.total_paise for line in lines.values()) > 0

    fallback = planning.lines_from_plan(profile(), cat, None, fallback=True)
    assert {line.reason_code for line in fallback} == {"daily_use"}


def test_the_meal_plan_days_show_recipes_sides_and_tiffins() -> None:
    cat = catalog()
    plan = [
        {"day": 0, "slot": "lunch", "recipe": "dal-tadka"},
        {"day": 6, "slot": "dinner", "recipe": None},
    ]

    days = planning.meal_plan_days(plan, cat, MONDAY)

    assert len(days) == 7 and days[0]["date"] == "2026-10-12"
    lunch = days[0]["meals"][1]
    assert (
        lunch["recipe_slug"] == "dal-tadka"
        and lunch["sides"] == ["chapati"]
        and not lunch["tiffin"]
    )
    assert days[6]["meals"][2]["tiffin"] is True
    assert planning.meal_plan_days(None, cat, MONDAY)[0]["meals"][0]["tiffin"] is False
    assert planning.highlights_for("fallback", [{"day": 1}], True) == [
        {"code": "festival", "params": {"days": [1]}},
        {"code": "budget_near", "params": {}},
        {"code": "fallback_rules", "params": {}},
    ]


def by_slug(cat: PlanCatalog, lines: list[planning.DraftLine]) -> dict[str, planning.DraftLine]:
    return {cat.products[line.product_id].slug: line for line in lines}


def test_fruit_grams_become_pieces_through_the_weight_of_one() -> None:
    cat = catalog()
    banana = cat.product_by_slug("banana")
    guava = cat.product_by_slug("guava")
    assert banana is not None and guava is not None

    lines = by_slug(cat, planning.lines_from_plan(profile(), cat, None, fallback=True))

    # 700 g a week x 3.2 adult equivalents ÷ 2 fruits = 1120 g of each.
    assert planning.in_base_units(guava, 1120) == 1120  # sold by weight: grams as they are
    assert planning.in_base_units(banana, 1120) == 1120 / 120  # 9.3 bananas of 120 g
    assert lines["banana"].qty == 2  # two dozen-halves of 6, not 1120 ÷ 6 = 187 steps
    assert lines["guava"].qty == 3  # 1120 g in 500 g steps
    unweighed = product("chikoo", "fruit", (6, "piece"))
    assert planning.in_base_units(unweighed, 1120) == 1120 / planning.DEFAULT_UNIT_G


def test_a_dry_staple_left_over_is_used_up_by_the_weekly_need_before_it_counts() -> None:
    # Bought 3 kg a week ago, the household uses 2.24 kg a week: 0.76 kg is left.
    assert planning.left_over([(1.0, 3000)], 2240) == 760
    # Two deliveries: 3 kg two weeks ago (0.76 kg left a week later) and 2 kg a week ago.
    assert planning.left_over([(1.0, 2000), (2.0, 3000)], 2240) == 520
    # Nothing goes below zero: an empty cupboard does not borrow from the next delivery.
    assert planning.left_over([(1.0, 1000), (3.0, 500)], 2240) == 0
    assert planning.left_over([], 2240) == 0

    cat = catalog()
    staple, potato, tomato = (cat.product_by_slug(s) for s in ("wheat-atta", "potato", "tomato"))
    assert staple is not None and potato is not None and tomato is not None
    assert planning.carries_over(staple)
    assert not planning.carries_over(potato) and not planning.carries_over(tomato)  # produce
    week = [{"day": 0, "slot": "dinner", "recipe": "tomato-sabzi"}]

    def atta_and_tomato(bought: dict[uuid.UUID, list[tuple[float, float]]]) -> tuple:
        lines = by_slug(
            cat, planning.lines_from_plan(profile(), cat, week, fallback=False, bought=bought)
        )
        return (lines["wheat-atta"].qty if "wheat-atta" in lines else 0, lines["tomato"].qty)

    # The rule table: 700 g x 3.2 = 2240 g of atta a week → 3 kg steps with nothing at home.
    assert atta_and_tomato({}) == (3, 1)
    # Last week's 3 kg left 760 g: the need minus the leftover is 1480 g → 2 steps, not none.
    assert atta_and_tomato({staple.id: [(1.0, 3000)]}) == (2, 1)
    # 6 kg a week ago still covers this week: no atta line.
    assert atta_and_tomato({staple.id: [(1.0, 6000)]}) == (0, 1)
    # Produce is bought fresh whatever came before.
    assert atta_and_tomato({tomato.id: [(1.0, 5000)]}) == (3, 1)
