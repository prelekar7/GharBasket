"""The box and the pantry (v1a): drafting, edits with their reasons, delivery, draw-down and
the taps; price shocks."""

import random
from collections import Counter
from datetime import date

from gharbasket_sim import config, pantry
from gharbasket_sim.catalog import Catalog
from gharbasket_sim.weeks import cpsat_planner, simulate

from gharbasket.modules.subscriptions import rules

from .test_weeks import households, pick

MONDAY = date(2026, 10, 5)


def shocks(**episodes: float) -> config.Shocks:
    return config.Shocks(
        about="test",
        episodes=[
            config.Shock(product=p, start_week=0, weeks=8, multiplier=m)
            for p, m in episodes.items()
        ],
    )


def test_prices_follow_the_episodes(catalog: Catalog) -> None:
    factor = pantry.weekly_prices(catalog, shocks(tomato=1.8), 3)
    assert factor["tomato"] == 1.8 and factor["onion"] == 1.0
    tomato = catalog.products["tomato"]
    assert pantry.price_of(tomato, factor) == round((tomato.price_paise or 0) * 1.8)
    assert pantry.weekly_prices(catalog, None, 0)["tomato"] == 1.0
    assert config.load_shocks().episodes  # the committed episodes parse


def test_the_draft_covers_the_dishes_the_rule_table_and_two_fruits(catalog: Catalog) -> None:
    h = households(catalog)[0]
    flat = pantry.weekly_prices(catalog, None, 0)

    lines = pantry.draft_box(h, catalog, ["dal-tadka", "bhindi-masala"], flat)

    by_product = {line.product: line for line in lines}
    need = sum(i.qty for i in catalog.ingredients["dal-tadka"] if i.product == "toor-dal")
    assert by_product["toor-dal"].base_units >= need * h.adult_equivalents
    assert by_product["toor-dal"].reason == "meal_plan" and by_product["toor-dal"].source == "ai"
    assert by_product["milk"].reason == "daily_use" and by_product["milk"].source == "rule"
    assert Counter(line.reason for line in lines)["in_season"] == 2
    assert all(line.qty >= 1 and line.unit_price_paise > 0 for line in lines)
    assert pantry.total_paise(lines) == sum(line.qty * line.unit_price_paise for line in lines)


def test_edits_follow_taste_stock_and_price(catalog: Catalog) -> None:
    h = households(catalog)[0]
    spiked = pantry.weekly_prices(catalog, shocks(tomato=2.0), 0)
    drafted = pantry.draft_box(h, catalog, ["dal-tadka", "bhindi-masala"], spiked)
    cupboard = pantry.Pantry()
    cupboard.stock["wheat-atta"] = 10_000  # ten kilos of atta: no need to buy any

    final, edits = pantry.edit_box(
        random.Random(1),
        h,
        catalog,
        drafted,
        planned=["dal-tadka", "bhindi-masala"],
        cooked=["bhindi-masala", "palak-dal"],  # dal tadka was swapped for palak dal
        pantry=cupboard,
        factor=spiked,
        price_sensitivity=1.0,
    )

    reasons = {(e["product"], e["action"]): e["reason"] for e in edits}
    assert reasons[("wheat-atta", "removed")] == "stock"
    assert not any(e["product"] in ("toor-dal", "palak") for e in edits)  # a meal swap, no label
    tomato_edit = next(e for e in edits if e["product"] == "tomato")
    assert tomato_edit["reason"] == "price" and tomato_edit["action"] in ("swapped", "qty_down")
    products = {line.product for line in final}
    assert "toor-dal" not in products and "wheat-atta" not in products and "palak" in products
    if tomato_edit["action"] == "swapped":
        swapped = next(line for line in final if line.replaces == "tomato")
        assert swapped.reason == "swapped_by_you" and swapped.source == "customer"
        assert swapped.unit_price_paise < (pantry.price_of(catalog.products["tomato"], spiked) or 0)


def test_the_pantry_runs_out_wastes_perishables_and_taps(catalog: Catalog) -> None:
    cupboard = pantry.Pantry()
    flat = pantry.weekly_prices(catalog, None, 0)
    milk, palak = catalog.products["milk"], catalog.products["palak"]
    pantry.deliver(
        cupboard,
        [
            pantry.Line("milk", 2, milk.step, 3000, "daily_use", "rule"),
            pantry.Line("palak", 2, palak.step, 2500, "meal_plan", "ai"),
        ],
    )
    assert cupboard.stock["milk"] == 2 * milk.step and flat["milk"] == 1.0

    rows = pantry.consume(
        random.Random(3),
        cupboard,
        catalog,
        {"milk": 3 * milk.step, "palak": 0.5 * palak.step},
        {"milk": 2 * milk.step, "palak": 2 * palak.step},
        feedback_rate=1.0,
    )

    by_product = {row["product"]: row for row in rows}
    assert (
        by_product["milk"]["bought_elsewhere"] == milk.step
        and by_product["milk"]["signal"] == "ran_out"
    )
    assert (
        by_product["palak"]["wasted"] == 1.5 * palak.step
        and by_product["palak"]["signal"] == "wasted"
    )
    assert cupboard.stock["milk"] == 0 and cupboard.stock["palak"] == 0
    quiet = pantry.consume(random.Random(3), cupboard, catalog, {}, {}, feedback_rate=1.0)
    assert quiet == []


def test_a_week_writes_boxes_lines_edits_and_pantry_rows(catalog: Catalog) -> None:
    h = pick(catalog, diet="veg")

    rows = simulate(7, h, catalog, MONDAY, 2, cpsat_planner, shocks(tomato=1.8), feedback_rate=1.0)
    again = simulate(7, h, catalog, MONDAY, 2, cpsat_planner, shocks(tomato=1.8), feedback_rate=1.0)

    assert rows.boxes == again.boxes and rows.pantry == again.pantry  # deterministic
    assert [b["week"] for b in rows.boxes] == [0, 1]
    for box in rows.boxes:
        lines = [line for line in rows.box_lines if line["week"] == box["week"]]
        assert box["lines"] == len(lines)
        assert box["total_paise"] == sum(line["qty"] * line["unit_price_paise"] for line in lines)
        assert box["edits"] == sum(e["week"] == box["week"] for e in rows.edits)
    assert {row["signal"] for row in rows.pantry if row["delivered"] > 0} <= {
        "ran_out",
        "wasted",
        "just_right",
    }
    delivered_weeks = {row["week"] for row in rows.pantry if row["delivered"] > 0}
    assert delivered_weeks == {b["week"] for b in rows.boxes if b["ordered"]}


def test_a_swap_is_same_unit_cheaper_in_total_and_is_cooked(catalog: Catalog) -> None:
    h = households(catalog)[0]
    onion = pantry.weekly_prices(catalog, shocks(onion=1.5), 0)
    line = next(
        line
        for line in pantry.draft_box(h, catalog, ["dal-tadka"] * 7, onion)
        if line.product == "onion"
    )
    # ginger is dearer per gram than onion even at 1.5x: no 2 kg of ginger for the onion
    found = pantry.cheaper_substitute(catalog, line, onion)
    assert found is None or found[0].slug != "ginger"

    for slug, product in sorted(catalog.products.items()):
        spiked = pantry.weekly_prices(catalog, shocks(**{slug: 2.0}), 0)
        price = pantry.price_of(product, spiked)
        if price is None:
            continue
        original = pantry.Line(slug, 3, product.step, price, "meal_plan", "ai")
        found = pantry.cheaper_substitute(catalog, original, spiked)
        if found is None:
            continue
        other, swapped = found
        assert other.unit == product.unit and other.category == product.category
        assert swapped.unit_price_paise / swapped.step < price / product.step
        assert swapped.total_paise < original.total_paise
        assert swapped.base_units >= original.base_units and swapped.replaces == slug

    cupboard = pantry.Pantry()
    leaves = catalog.products["curry-leaves"]
    pantry.deliver(
        cupboard,
        [
            pantry.Line(
                "curry-leaves",
                3,
                leaves.step,
                1000,
                "swapped_by_you",
                "customer",
                replaces="coriander",
            )
        ],
    )
    rows = pantry.consume(
        random.Random(1),
        cupboard,
        catalog,
        {"coriander": 2.0},
        {"curry-leaves": 3.0},
        feedback_rate=1.0,
        stand_ins={"coriander": "curry-leaves"},
    )
    by_product = {row["product"]: row for row in rows}
    assert "coriander" not in by_product  # none bought elsewhere: the substitute was cooked
    assert by_product["curry-leaves"]["used"] == 2.0
    assert by_product["curry-leaves"]["wasted"] == 1.0  # perishable: the spare bunch


def test_a_daily_use_ingredient_gets_the_recipe_and_the_daily_quantity(catalog: Catalog) -> None:
    h = households(catalog)[0]
    flat = pantry.weekly_prices(catalog, None, 0)
    curd = catalog.products["curd"]
    daily = rules.daily_use_plan(h.adult_equivalents)["curd"]
    recipe = 7 * 100 * h.adult_equivalents  # gujarati kadhi every day: 100 g a serving

    drafted = pantry.draft_box(h, catalog, ["gujarati-kadhi"] * 7, flat)

    line = next(line for line in drafted if line.product == "curd")
    assert line.qty == pantry.steps(curd, recipe + daily)
    assert line.reason == "daily_use" and line.source == "rule"  # as M3's lines_from_plan
    assert len({line.product for line in drafted}) == len(drafted)  # one line a product

    final, edits = pantry.edit_box(
        random.Random(1),
        h,
        catalog,
        drafted,
        planned=["gujarati-kadhi"] * 7,
        cooked=["dal-tadka"] * 7,  # every kadhi swapped away: only the daily curd is left
        pantry=pantry.Pantry(),
        factor=flat,
        price_sensitivity=0.0,
    )
    kept = next(line for line in final if line.product == "curd")
    assert kept.qty == pantry.steps(curd, daily)
    assert not any(e["product"] == "curd" for e in edits)


def test_a_stock_edit_is_recorded_only_when_the_quantity_changes(catalog: Catalog) -> None:
    h = households(catalog)[0]
    flat = pantry.weekly_prices(catalog, None, 0)
    sugar = catalog.products["sugar"]
    for seed in range(40):
        cupboard = pantry.Pantry()
        cupboard.stock["sugar"] = 1.5 * sugar.step  # one spare step: keep at least one
        line = pantry.Line("sugar", 1, sugar.step, 5000, "daily_use", "rule")
        final, edits = pantry.edit_box(
            random.Random(seed),
            h,
            catalog,
            [line],
            planned=[],
            cooked=[],
            pantry=cupboard,
            factor=flat,
            price_sensitivity=0.0,
        )
        sugar_edits = [e for e in edits if e["product"] == "sugar"]
        assert all(e["action"] == "removed" for e in sugar_edits)
        if not sugar_edits:
            assert any(f.product == "sugar" and f.qty == 1 for f in final)


def test_staples_carry_over_within_their_shelf_life(catalog: Catalog) -> None:
    salt, tomato = catalog.products["salt"], catalog.products["tomato"]
    assert not tomato.perishable and (tomato.shelf_life_h or 0) < pantry.WEEK_H
    cupboard = pantry.Pantry()
    pantry.deliver(
        cupboard,
        [
            pantry.Line("salt", 1, salt.step, 2500, "daily_use", "rule"),
            pantry.Line("tomato", 1, tomato.step, 4000, "meal_plan", "ai"),
        ],
    )
    first = pantry.consume(
        random.Random(1),
        cupboard,
        catalog,
        {},
        {"salt": salt.step, "tomato": tomato.step},
        feedback_rate=0.0,
    )
    assert {row["product"]: row["wasted"] for row in first} == {"salt": 0, "tomato": tomato.step}
    for _ in range(6):  # salt keeps for two years, not three weeks
        rows = pantry.consume(random.Random(1), cupboard, catalog, {}, {}, feedback_rate=0.0)
        assert all(row["wasted"] == 0 for row in rows)
    assert cupboard.stock["salt"] == salt.step
