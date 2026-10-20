"""Daily nutrition targets per person (ICMR-NIN RDA 2020, data/external/nutrition/rda.json) for
the planner's nutrition term (M10 §3.2): a day is "adherent" when the three meals' kcal and
protein per person fall within ±20 % of the household's average target.

The numbers are the hypothesis registered in handbook 08 §8.5 until ratified.
"""

from dataclasses import dataclass

from gharbasket_ml.schemas import HouseholdRules, RecipeIn

TOLERANCE = 0.2
RDA = {
    "under_5": {"kcal": 1240.0, "protein_g": 14.0},
    "5_17": {"kcal": 2300.0, "protein_g": 36.0},
    "18_59": {"kcal": 1885.0, "protein_g": 50.0},
    "60_plus": {"kcal": 1700.0, "protein_g": 48.0},
}
DEFAULT_BAND = "18_59"


@dataclass(frozen=True)
class Target:
    kcal: float
    protein_g: float

    def within(self, kcal: float, protein_g: float) -> bool:
        return (1 - TOLERANCE) * self.kcal <= kcal <= (1 + TOLERANCE) * self.kcal and (
            1 - TOLERANCE
        ) * self.protein_g <= protein_g <= (1 + TOLERANCE) * self.protein_g


def target(household: HouseholdRules) -> Target:
    """The average daily target of the household's members (one adult when unknown)."""
    bands = [m.age_band for m in household.members] if household.members else [DEFAULT_BAND]
    kcal = sum(RDA.get(b, RDA[DEFAULT_BAND])["kcal"] for b in bands) / len(bands)
    protein = sum(RDA.get(b, RDA[DEFAULT_BAND])["protein_g"] for b in bands) / len(bands)
    return Target(kcal, protein)


def per_person(recipe: RecipeIn, sides: dict[str, RecipeIn]) -> tuple[float, float] | None:
    """kcal and protein a person gets from one meal of this dish with its sides; None when the
    dish (or a side) has no nutrition data."""
    if recipe.nutrition_per_serving is None:
        return None
    kcal = recipe.nutrition_per_serving.kcal
    protein = recipe.nutrition_per_serving.protein_g
    for slug in recipe.sides:
        side = sides.get(slug)
        if side is None or side.nutrition_per_serving is None:
            continue
        kcal += side.nutrition_per_serving.kcal
        protein += side.nutrition_per_serving.protein_g
    return kcal, protein
