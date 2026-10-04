"""The weekly plan request and answer, as M10 §3.1 defines them for POST /plan/week.

The ML service (backend/ml) has its own copy of these shapes; tests run one against the other.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Slot = Literal["breakfast", "lunch", "dinner"]
Rule = Literal["repeat", "cook_time", "budget"]


class PlanMember(BaseModel):
    age_band: Literal["under_5", "5_17", "18_59", "60_plus"]
    sex: Literal["f", "m", "other"] = "other"


class HouseholdRules(BaseModel):
    size: int = Field(ge=1, le=12)
    diet: Literal["veg", "egg", "nonveg"]
    jain: bool = False
    diabetic: bool = False
    cuisines: list[str] = Field(default_factory=list)
    weekday_cook_minutes: int
    weekend_cook_minutes: int
    no_cook_days: list[int] = Field(default_factory=list)
    weekly_budget_paise: int | None = None
    members: list[PlanMember] | None = None  # v1: RDA targets


class FastingDay(BaseModel):
    day: int = Field(ge=0, le=6)
    no_onion_garlic: bool = False


class PlanNutrition(BaseModel):
    kcal: float
    protein_g: float
    fibre_g: float = 0


class PlanRecipe(BaseModel):
    slug: str
    cuisine: str
    slots: list[Slot]
    cook_minutes: int
    diet: Literal["veg", "egg", "nonveg"]
    jain_ok: bool
    no_onion_garlic: bool
    fasting_ok: bool
    diabetic_ok: bool
    no_cook: bool = False
    cost_per_serving_paise: int
    perishables: list[str] = Field(default_factory=list)
    # v1 (M10 §3.2), optional: nutrition for the RDA term, sides paired with the main, sides
    # flagged so they are never planned as meals, the price spike of the costliest ingredient.
    nutrition_per_serving: PlanNutrition | None = None
    sides: list[str] = Field(default_factory=list)
    is_side: bool = False
    price_spike: float | None = Field(default=None, ge=0)


class PlanWeights(BaseModel):
    preference: int = 3
    variety: int = 5
    perishables: int = 4
    nutrition: int = 4
    price: int = 3


class PlanRequest(BaseModel):
    household: HouseholdRules
    fasting_days: list[FastingDay] = Field(default_factory=list)
    recipes: list[PlanRecipe] = Field(min_length=1)
    time_limit_s: float = 2.0
    weights: PlanWeights | None = None


Reason = Literal["FESTIVAL", "TIFFIN", "BUDGET", "FALLBACK"]


class PlannedMeal(BaseModel):
    day: int
    slot: Slot
    recipe: str | None
    sides: list[str] = Field(default_factory=list)
    reasons: list[Reason] = Field(default_factory=list)


class PlanScore(BaseModel):
    preference: int
    variety: int
    perishables: int
    cost_paise: int
    nutrition: float | None = None
    price_penalty: float | None = None


class WeekPlan(BaseModel):
    """The plan, and who made it: the ML service, or the rule-based fallback."""

    source: Literal["ml", "fallback"]
    status: Literal["optimal", "feasible", "infeasible"]
    relaxed: list[Rule]
    plan: list[PlannedMeal] | None
    model: str
    score: PlanScore | None = None


# --- POST /pantry/quantities (M10 §3.3): the pantry twin ---------------------------------------

Tap = Literal["ran_out", "wasted", "just_right"]


class PantryObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    qty: float = Field(ge=0, description="What the week's box carried, in base units")
    signal: Tap | None = None


class PantryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product: str = Field(min_length=1, max_length=80)
    step: float = Field(gt=0)
    prior_mean: float = Field(ge=0)
    prior_weeks: float = Field(default=2.0, ge=0, le=52)
    observations: list[PantryObservation] = Field(default_factory=list, max_length=52)


class PantryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[PantryItem] = Field(min_length=1, max_length=300)


class PantryQuantity(BaseModel):
    product: str
    mean: float
    p10: float
    p90: float
    weeks_observed: int
    alpha: float
    beta: float


class PantryQuantities(BaseModel):
    source: Literal["ml", "fallback"]
    model: str
    items: list[PantryQuantity]
