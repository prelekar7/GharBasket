from collections import Counter

import pytest
from gharbasket_sim import config
from gharbasket_sim.catalog import Catalog
from gharbasket_sim.households import PHONE_PREFIX, sample, societies
from pydantic import ValidationError

from gharbasket.modules.identity.schemas import HouseholdIn


def test_the_same_seed_gives_the_same_households_and_another_seed_others(catalog: Catalog) -> None:
    cfg = config.load()

    first, again, other = (sample(s, cfg, catalog) for s in (7, 7, 8))

    assert first == again
    assert [h.body for h in first] != [h.body for h in other]


def test_households_fill_a_tenth_of_each_societys_flats(catalog: Catalog) -> None:
    cfg = config.load()

    households = sample(7, cfg, catalog)
    fewer = sample(7, cfg, catalog, adoption=0.02)

    expected = sum(round((s.flats or 0) * cfg.adoption) for s in societies())
    assert len(households) == expected == 87
    assert len(fewer) == sum(round((s.flats or 0) * 0.02) for s in societies())
    per_society = Counter(h.society_id for h in households)
    assert set(per_society) == {str(s.id) for s in societies()}


def test_every_household_is_a_valid_onboarding_request(catalog: Catalog) -> None:
    households = sample(7, config.load(), catalog)
    towers = {str(s.id): {t["id"] for t in s.towers} for s in societies()}

    for h in households:
        body = HouseholdIn.model_validate(h.body)
        assert body.address.tower_id in towers[h.society_id]
        assert h.rules["size"] == len(body.members)
    flats = [(h.society_id, h.body["address"]["flat"]) for h in households]
    assert len(set(flats)) == len(flats)
    phones = [h.phone for h in households]
    assert len(set(phones)) == len(phones) and all(p.startswith(PHONE_PREFIX) for p in phones)


def test_rules_are_consistent_with_the_members(catalog: Catalog) -> None:
    households = sample(7, config.load(), catalog)

    jain = [h for h in households if h.rules["jain"]]
    diabetic = [h for h in households if h.rules["diabetic"]]
    assert jain and diabetic
    for h in jain:
        assert h.rules["diet"] == "veg"
        assert all(m["fasting_rules"]["jain"] for m in h.body["members"])
    for h in diabetic:
        assert any("diabetes" in m.get("health_tags", []) for m in h.body["members"])
        assert "health_personalisation" in {c["purpose"] for c in h.body["consents"]}
    for h in households:
        budget = h.body["budget_paise"]
        assert h.rules["weekly_budget_paise"] == (None if budget is None else budget * 7 // 30)


def test_latent_truth_covers_daily_use_fruit_and_every_recipe(catalog: Catalog) -> None:
    h = sample(7, config.load(), catalog)[0]

    assert set(h.taste) == {r["slug"] for r in catalog.recipes}
    assert {"milk", "wheat-atta"} <= set(h.daily_use)
    # Fruit truth is in each product's base unit; back in grams it is the weekly basket.
    grams = sum(
        h.daily_use[f]
        * (1 if catalog.products[f].unit in ("g", "ml") else catalog.products[f].unit_g or 100)
        for f in catalog.fruits()
    )
    assert grams == pytest.approx(700 * h.adult_equivalents)


def test_a_cohort_without_adults_or_seniors_is_rejected() -> None:
    cohort = config.load().cohorts[0].model_dump()

    with pytest.raises(ValidationError, match="needs an adult or a senior"):
        config.Cohort.model_validate({**cohort, "adults": (0, 0), "seniors": (0, 0)})
