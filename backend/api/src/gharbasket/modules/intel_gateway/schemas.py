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


# --- POST /route/solve (M10 §3.4): one delivery slot's van routes ------------------------------


class RoutePoint(BaseModel):
    lat: float
    lng: float


class RouteFoodBank(RoutePoint):
    window_start_s: int
    window_end_s: int


class RouteStop(BaseModel):
    id: str
    lat: float
    lng: float
    orders: int = 0
    kg_tenths: int = 0
    crates: int = 0
    empties: int = 0
    donation_kg_tenths: int = 0
    window_start_s: int
    window_end_s: int


class RouteVans(BaseModel):
    count: int
    capacity_kg: int = 400
    capacity_crates: int = 24
    traffic_factor: float = 1.5
    reload_s: int = 1200


class RouteRequest(BaseModel):
    hub: RoutePoint
    depart_s: int
    return_by_s: int
    stops: list[RouteStop] = Field(min_length=1)
    food_bank: RouteFoodBank | None = None
    vans: RouteVans
    time_limit_s: float = 10.0


class RouteStopOut(BaseModel):
    id: str
    kind: Literal["drop", "bank"]
    eta_s: int
    late_s: int = 0


class RouteVanOut(BaseModel):
    van: int
    start_s: int
    end_s: int
    stops: list[RouteStopOut]


class RouteSummary(BaseModel):
    vans: int
    km: float
    cost_paise: int
    on_time: float
    late_min: float
    problems: list[str] = Field(default_factory=list)


class RoutePlan(BaseModel):
    """The ML service's routes; `source` says whether the road network or straight lines priced
    them. None from the gateway means the service did not answer: M7 keeps yesterday's order."""

    status: Literal["solved", "fallback"]
    source: Literal["osrm", "haversine"]
    routes: list[RouteVanOut]
    summary: RouteSummary
    seconds: float
    model: str


# --- POST /assign/kitchens (M10 §3.5): tiffin portions to home kitchens ------------------------


class TiffinOrder(BaseModel):
    id: str
    lat: float
    lng: float
    portions: int
    skills: list[str] = Field(default_factory=list)


class Kitchen(BaseModel):
    id: str
    lat: float
    lng: float
    max_portions: int
    skills: list[str] = Field(default_factory=list)
    rating: float = 4.5
    earnings_paise: int = 0


class AssignRequest(BaseModel):
    orders: list[TiffinOrder] = Field(min_length=1)
    kitchens: list[Kitchen] = Field(min_length=1)
    fairness_lambda: float = 0.5
    rating_floor: float = 3.8
    max_km: float = 5.0
    paise_per_portion: int = 6000
    time_limit_s: float = 5.0


class Assignment(BaseModel):
    order_id: str
    kitchen_id: str
    portions: int
    km: float


class AssignSummary(BaseModel):
    portions: int
    kitchens_used: int
    km_per_portion: float
    cost_paise_per_portion: int
    gini: float
    earnings_paise: list[int]


class KitchenAssignment(BaseModel):
    status: Literal["solved", "fallback", "infeasible"]
    assignments: list[Assignment]
    summary: AssignSummary | None
    seconds: float
    model: str
