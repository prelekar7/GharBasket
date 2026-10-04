"""A recipe's diet, Jain and onion/garlic rules come from its ingredients' tags (M2 §3.2)."""

import pytest

from gharbasket.modules.catalog.recipes import derived_rules


@pytest.mark.parametrize(
    ("tags", "expected"),
    [
        ([{"leafy"}, {"high_protein"}], ("veg", True, True)),
        ([{"root"}, set()], ("veg", False, True)),  # potato: no onion, but not Jain
        ([{"root", "onion_garlic"}], ("veg", False, False)),
        ([{"egg", "high_protein"}], ("egg", False, True)),  # Jain food has no eggs
        ([], ("veg", True, True)),
    ],
)
def test_diet_rules_come_from_the_ingredients(
    tags: list[set[str]], expected: tuple[str, bool, bool]
) -> None:
    assert derived_rules(tags) == expected
