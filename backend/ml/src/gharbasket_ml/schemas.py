"""Request and response of POST /plan/week (M10 §3.1) and POST /pantry/quantities (§3.3)."""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


# --- POST /route/solve (M10 §3.4): one delivery slot's van routes -------------------------------


class Point(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class FoodBankIn(Point):
    window_start_s: int = Field(ge=0)
    window_end_s: int = Field(ge=0)

    @model_validator(mode="after")
    def _window_in_order(self) -> Self:
        if self.window_start_s > self.window_end_s:
            raise ValueError("food_bank: window_start_s is after window_end_s")
        return self


class StopIn(BaseModel):
    """A society stop: what the van drops and picks up, inside the captain's window.
    Times are seconds after the slot day's midnight IST; weights in tenths of a kg."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=80, description="The society id (opaque here)")
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    orders: int = Field(default=0, ge=0)
    kg_tenths: int = Field(default=0, ge=0)
    crates: int = Field(default=0, ge=0)
    empties: int = Field(default=0, ge=0)
    donation_kg_tenths: int = Field(default=0, ge=0)
    window_start_s: int = Field(ge=0)
    window_end_s: int = Field(ge=0)

    @model_validator(mode="after")
    def _window_in_order(self) -> Self:
        if self.window_start_s > self.window_end_s:
            raise ValueError(f"stop {self.id}: window_start_s is after window_end_s")
        return self


class VansIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int = Field(ge=1, le=60, description="Vans available (the solver uses fewer)")
    capacity_kg: int = Field(default=400, ge=1)
    capacity_crates: int = Field(default=24, ge=1)
    traffic_factor: float = Field(default=1.5, ge=1.0, le=3.0, description="times the road time")
    reload_s: int = Field(default=1200, ge=0)


class ServiceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    drop_s: int = Field(default=240, ge=0, description="Handover time at a society")
    per_crate_s: int = Field(default=30, ge=0)


class CostIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fixed_paise_per_van: int = Field(default=25_000, ge=0)
    paise_per_km: int = Field(default=60, ge=0)
    wage_paise_per_s: int = Field(default=3, ge=0)
    late_paise_per_s: int = Field(default=50, ge=0)


class RouteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hub: Point
    depart_s: int = Field(ge=0, description="Vans are loaded by then (06:30 → 23400)")
    return_by_s: int = Field(ge=0, description="Back at the hub (11:30 → 41400)")
    stops: list[StopIn] = Field(min_length=1, max_length=400)
    food_bank: FoodBankIn | None = None
    vans: VansIn
    service: ServiceIn = Field(default_factory=ServiceIn)
    cost: CostIn = Field(default_factory=CostIn)
    time_limit_s: float = Field(default=10.0, ge=0.5, le=20.0, description="Both waves together")
    max_late_s: int = Field(
        default=1800,
        ge=0,
        description="A drop later than this past its window's end is a checker problem",
    )

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.depart_s >= self.return_by_s:
            raise ValueError("depart_s must be before return_by_s")
        if self.food_bank is None and any(s.donation_kg_tenths for s in self.stops):
            raise ValueError("donations need a food_bank to take them to")
        return self


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
    problems: list[str] = Field(default_factory=list, description="The checker's findings")


class RouteResponse(BaseModel):
    status: Literal["solved", "fallback"]
    source: Literal["osrm", "haversine"]
    routes: list[RouteVanOut]
    summary: RouteSummary
    seconds: float
    model: str = "cvrptw-waves-v0"


# --- POST /assign/kitchens (M10 §3.5): tomorrow's tiffin portions to home kitchens ------------

FAIRNESS_LAMBDA = 300.0  # paise per portion of imbalance: the knee of the test sweep (M10 §3.5)
MAX_ORDERS_FOR_SOLVER = 500  # measured: up to 500x60 ends within ~2 % of optimal in 5 s


class TiffinOrderIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=80, description="The order id (opaque here)")
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    portions: int = Field(ge=1, le=200)
    skills: list[str] = Field(default_factory=list, description="Menu skills the order needs")


class KitchenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=80)
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)
    max_portions: int = Field(ge=0, le=1000, description="Capacity for this meal")
    skills: list[str] = Field(default_factory=list)
    rating: float = Field(default=4.5, ge=0, le=5)
    earnings_paise: int = Field(
        default=0, ge=0, le=10**12, description="Rolling earnings before tonight"
    )


class AssignRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    orders: list[TiffinOrderIn] = Field(min_length=1, max_length=2000)
    kitchens: list[KitchenIn] = Field(min_length=1, max_length=300)
    fairness_lambda: float = Field(
        default=FAIRNESS_LAMBDA,
        ge=0,
        le=10_000,
        description="Paise of cost per portion of earnings imbalance; 0 = cost only",
    )
    rating_floor: float = Field(default=3.8, ge=0, le=5)
    max_km: float = Field(default=5.0, gt=0, le=50)
    paise_per_portion: int = Field(
        default=6000, ge=1, le=100_000, description="What a kitchen earns"
    )
    quality_paise_per_star: int = Field(
        default=500, ge=0, le=10_000, description="Penalty per portion per star below 5"
    )
    max_kitchens_per_order: int = Field(
        default=15, ge=1, le=300, description="Only the nearest this many kitchens are candidates"
    )
    time_limit_s: float = Field(
        default=5.0, ge=0.1, le=20.0, description="Wall clock, model building included"
    )
    max_orders_for_solver: int = Field(
        default=MAX_ORDERS_FOR_SOLVER, ge=1, le=2000, description="Above this, greedy answers"
    )


class AssignmentOut(BaseModel):
    order_id: str
    kitchen_id: str
    portions: int
    km: float


class UnassignedOut(BaseModel):
    order_id: str
    reason: Literal[
        "below_rating_floor", "no_skills", "too_many_portions", "out_of_reach", "no_room"
    ]


class KitchenEarningsOut(BaseModel):
    kitchen_id: str
    earnings_paise: int = Field(description="Rolling earnings after tonight")


class AssignSummary(BaseModel):
    portions: int = Field(description="Assigned portions")
    kitchens_used: int
    km_per_portion: float
    cost_paise_per_portion: int
    gini: float = Field(description="Of every kitchen's rolling earnings after tonight")
    gini_eligible: float = Field(
        description="Of the kitchens that could take an order tonight: what λ balances"
    )
    earnings_paise: list[KitchenEarningsOut]


class AssignResponse(BaseModel):
    status: Literal["solved", "partial", "fallback", "infeasible"]
    assignments: list[AssignmentOut]
    unassigned: list[UnassignedOut]
    summary: AssignSummary | None
    seconds: float
    model: str = "assignment-cpsat-v0"


# --- POST /basket/rank (M10 §3.7): the next-basket ranker -------------------------------------


class RankCandidate(BaseModel):
    """A candidate product with the features the training job defines (backend/training
    ranker.py); the API computes them from the household's past boxes."""

    model_config = ConfigDict(extra="forbid")

    product: str = Field(min_length=1, max_length=80)
    weeks_since: float = Field(
        ge=0,
        description=(
            "Weeks since last bought, capped at 52; 52 when never bought (the training job's "
            "value). Larger values are served as 52."
        ),
    )
    frequency: float = Field(ge=0, le=1, description="Share of past weeks it was bought")
    drafted: bool = False
    removed_last: bool = False
    popularity: float = Field(default=0, ge=0, le=1, description="Share of households last week")
    price_paise: int = Field(default=0, ge=0)


class RankRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[RankCandidate] = Field(min_length=1, max_length=500)
    top_k: int = Field(default=10, ge=1, le=100)


class RankedOut(BaseModel):
    product: str
    score: float
    rank: int


class RankResponse(BaseModel):
    source: Literal["ml", "fallback"]
    model: str
    ranked: list[RankedOut]


# --- POST /assistant/turn (M13 §3): one turn of Ghar Saathi -------------------------------------


class AssistantMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4000)


class WeekLine(BaseModel):
    """One line of the week as the assistant sees it: product facts and the API's prices."""

    model_config = ConfigDict(extra="forbid")

    handle: str = Field(min_length=1, max_length=8, description="Opaque, this turn only (L1, L2)")
    slug: str = Field(max_length=80)
    name: str = Field(max_length=120, description="In the person's language")
    qty: int = Field(ge=0, description="Steps")
    unit: str = Field(max_length=40, description="One step, e.g. 250 g")
    unit_price_paise: int = Field(ge=0)
    line_total_paise: int = Field(ge=0)
    reason_code: str = Field(max_length=40)


class WeekMeal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: str = Field(max_length=10)
    slot: str = Field(max_length=20)
    dishes: list[str] = Field(default_factory=list, max_length=10)


class WeekContext(BaseModel):
    """The trimmed week the API sends (M13 §3): no household, user, name or address."""

    model_config = ConfigDict(extra="forbid")

    delivery_date: str = Field(max_length=10)
    status: str = Field(max_length=20)
    cutoff_at: str = Field(max_length=40)
    can_edit: bool = Field(description="Still editable: an editable status, before the cut-off")
    total_paise: int = Field(ge=0)
    lines: list[WeekLine] = Field(default_factory=list, max_length=200)
    meal_plan: list[WeekMeal] = Field(default_factory=list, max_length=21)


class AssistantTurnRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    locale: str = Field(default="en-IN", max_length=10)
    messages: list[AssistantMessage] = Field(min_length=1, max_length=40)
    week: WeekContext | None = Field(default=None, description="null: no week planned yet")


class AssistantIntent(BaseModel):
    """A change the model asked for; the API resolves it into a proposal card (or says why not)."""

    action: Literal["edit_box_line", "skip_week"]
    op: Literal["remove", "add", "qty"] | None = None
    line: str | None = Field(default=None, description="The line's handle, for remove and qty")
    product: str | None = Field(default=None, description="The line's slug, or the name to add")
    qty: int | None = Field(default=None, description="As the model gave it; the API checks it")
    week: Literal["this", "next"] | None = Field(default=None, description="skip_week only")


class AssistantUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0


class AssistantTurnResponse(BaseModel):
    reply: str
    intents: list[AssistantIntent]
    tool_calls: list[str]
    usage: AssistantUsage
    model: str
    stop_reason: str
