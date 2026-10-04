"""The box and the pantry (v1a): drafting, edits with their reasons, delivery, draw-down and
the taps; price shocks."""

import random
from collections import Counter
from datetime import date

from gharbasket_sim import config, pantry
from gharbasket_sim.catalog import Catalog
from gharbasket_sim.weeks import cpsat_planner, simulate

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
