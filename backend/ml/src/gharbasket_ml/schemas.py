"""Request and response of POST /plan/week (M10 §3.1) and POST /pantry/quantities (§3.3)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Slot = Literal["breakfast", "lunch", "dinner"]
Rule = Literal["repeat", "cook_time", "budget"]


class Member(BaseModel):
    model_config = ConfigDict(extra="forbid")

    age_band: Literal["under_5", "5_17", "18_59", "60_plus"]
    sex: Literal["f", "m", "other"] = "other"


class HouseholdRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    size: int = Field(ge=1, le=12)
    members: list[Member] | None = Field(default=None, max_length=12, description="v1: RDA targets")
    diet: Literal["veg", "egg", "nonveg"]
    jain: bool = False
    diabetic: bool = False
    cuisines: list[str] = Field(default_factory=list, max_length=10)
    weekday_cook_minutes: int = Field(ge=1, le=600)
    weekend_cook_minutes: int = Field(ge=1, le=600)
    no_cook_days: list[int] = Field(default_factory=list, max_length=7)
    weekly_budget_paise: int | None = Field(default=None, ge=0)


class FastingDay(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day: int = Field(ge=0, le=6)
    no_onion_garlic: bool = False


class Nutrition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kcal: float = Field(ge=0)
    protein_g: float = Field(ge=0)
    fibre_g: float = Field(default=0, ge=0)


class RecipeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str = Field(min_length=1, max_length=80)
    cuisine: str
    slots: list[Slot] = Field(min_length=1)
    cook_minutes: int = Field(ge=1, le=600)
    diet: Literal["veg", "egg", "nonveg"]
    jain_ok: bool
    no_onion_garlic: bool
    fasting_ok: bool
    diabetic_ok: bool
    no_cook: bool = False
    cost_per_serving_paise: int = Field(ge=0)
    perishables: list[str] = Field(default_factory=list)
    # v1 (M10 §3.2), all optional: a v0 request gets a v0 answer.
    nutrition_per_serving: Nutrition | None = None
    sides: list[str] = Field(default_factory=list, description="Side recipes this main comes with")
    is_side: bool = Field(default=False, description="A side: never planned as a meal")
    price_spike: float | None = Field(
        default=None, ge=0, description="(price - 4-week index) / index of the costliest ingredient"
    )


class Weights(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preference: int = Field(default=3, ge=0)
    variety: int = Field(default=5, ge=0)
    perishables: int = Field(default=4, ge=0)
    nutrition: int = Field(default=4, ge=0)
    price: int = Field(default=3, ge=0)


class PlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    household: HouseholdRules
    fasting_days: list[FastingDay] = Field(default_factory=list, max_length=7)
    recipes: list[RecipeIn] = Field(min_length=1, max_length=500)
    time_limit_s: float = Field(
        default=1.0,
        ge=0.1,
        le=5.0,
        description="Solver wall-clock budget for the whole request, every relaxation included",
    )
    weights: Weights | None = Field(default=None, description="v1; evaluation ablations only")


Reason = Literal["FESTIVAL", "TIFFIN", "BUDGET", "FALLBACK"]


class PlannedMeal(BaseModel):
    day: int
    slot: Slot
    recipe: str | None = Field(description="null = tiffin")
    sides: list[str] = Field(default_factory=list)
    reasons: list[Reason] = Field(default_factory=list)


class Score(BaseModel):
    preference: int
    variety: int
    perishables: int
    cost_paise: int
    nutrition: float | None = Field(
        default=None, description="v1: share of cooking days within ±20 % (no-cook days left out)"
    )
    price_penalty: float | None = Field(default=None, description="v1: sum of chosen price spikes")


class PlanResponse(BaseModel):
    status: Literal["optimal", "feasible", "infeasible"]
    relaxed: list[Rule]
    plan: list[PlannedMeal] | None
    score: Score | None
    seconds: float
    model: str = "cpsat-v0"


# --- POST /pantry/quantities (M10 §3.3) --------------------------------------------------------

Tap = Literal["ran_out", "wasted", "just_right"]


class PantryObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    qty: float = Field(
        ge=0,
        description="The week's daily-use share in base units: what the box delivered minus "
        "what that week's planned recipes needed",
    )
    signal: Tap | None = Field(default=None, description="The household's tap for that week")


class PantryItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product: str = Field(min_length=1, max_length=80, description="Product slug")
    step: float = Field(gt=0, description="Base units in one step (the count unit)")
    prior_mean: float = Field(ge=0, description="The cohort's weekly use in base units")
    prior_weeks: float = Field(default=2.0, ge=0, le=52, description="The prior's weight")
    observations: list[PantryObservation] = Field(default_factory=list, max_length=52)


class PantryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[PantryItemIn] = Field(min_length=1, max_length=300)


class PantryItemOut(BaseModel):
    product: str
    mean: float = Field(description="Weekly use to plan for, in base units")
    p10: float = Field(description="Next week's use, 10th percentile (posterior predictive)")
    p90: float = Field(description="Next week's use, 90th percentile (posterior predictive)")
    weeks_observed: int
    alpha: float
    beta: float


class PantryResponse(BaseModel):
    items: list[PantryItemOut]
    model: str = "pantry-gamma-poisson-v0"
