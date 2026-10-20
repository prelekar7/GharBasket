"""The family-size rule table (M3 §3.1 step 2; handbook 08 M-A's baseline).

How much of each daily-use item a household goes through in a week, per adult equivalent, in
the product's base unit (g, ml, piece). The pantry twin (M10) replaces these quantities with
learned ones per household; until then, and whenever it cannot answer, this is the box.
The numbers match the simulator's cohorts, so the paper's baseline is this very table.
"""

from dataclasses import dataclass

# Adult equivalents by age band (M1 `age_band`).
ADULT_EQUIVALENT = {"under_5": 0.4, "5_17": 0.8, "18_59": 1.0, "60_plus": 0.85}

# Product slug -> base units per adult equivalent per week.
DAILY_USE_PER_ADULT_WEEK: dict[str, float] = {
    "milk": 1400,  # ml
    "wheat-atta": 700,  # g
    "curd": 250,  # g
    "sugar": 150,  # g
    "groundnut-oil": 120,  # ml
    "salt": 35,  # g
}
# Fruit: one basket a week, split over whatever is in season (g per adult equivalent).
FRUIT_PER_ADULT_WEEK_G = 700
FRUIT_KINDS = 2  # how many different fruits a box carries


@dataclass(frozen=True)
class Member:
    age_band: str


def adult_equivalents(members: list[Member]) -> float:
    return round(sum(ADULT_EQUIVALENT.get(m.age_band, 1.0) for m in members), 2) or 1.0


def steps_needed(base_units: float, step_qty: int, step_unit_factor: int) -> int:
    """How many steps cover `base_units` (e.g. 1400 ml of milk in 500 ml steps -> 3)."""
    per_step = step_qty * step_unit_factor
    if per_step <= 0:
        return 0
    steps = -(-base_units // per_step)  # ceiling
    return int(max(1, steps))


def cohort_of(equivalents: float) -> str:
    """The pantry twin's household-size cohort (M10 §3.3 v1): the learned priors are per cohort."""
    if equivalents < 2.0:
        return "small"
    if equivalents < 3.5:
        return "medium"
    return "large"


def daily_use_plan(equivalents: float) -> dict[str, float]:
    """Base units per week of each daily-use product for a household of `equivalents`."""
    return {slug: rate * equivalents for slug, rate in DAILY_USE_PER_ADULT_WEEK.items()}
