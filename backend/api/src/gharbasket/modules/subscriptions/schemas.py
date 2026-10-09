"""Request and response models of M3 Plans & Subscriptions: the S3 contract (spec §3.1), the
meal swap (§3.2), and a week that failed to pay (§3.3)."""

import uuid
from datetime import date, datetime, time
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PlanCode = Literal["autopilot", "tiffin", "salad"]
PaymentMode = Literal["wallet", "autopay"]
SubscriptionStatus = Literal["active", "paused", "ended"]
CycleStatus = Literal[
    "draft",
    "awaiting_approval",
    "approved",
    "skipped",
    "cancelled",
    "locked",
    "payment_failed",
    "preview",
]
LineSource = Literal["ai", "rule", "customer"]
Unit = Literal["g", "kg", "ml", "l", "piece", "bunch", "pack"]
Slot = Literal["breakfast", "lunch", "dinner"]


class PlanOut(BaseModel):
    code: PlanCode
    name: str
    fee_paise: int
    features: list[str]


class PlansOut(BaseModel):
    items: list[PlanOut]


class SubscriptionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: PlanCode = "autopilot"
    payment_mode: PaymentMode = "wallet"


class SubscriptionPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paused_until: date | None = Field(default=None, description="null resumes")
    auto_approve: bool | None = None


class SubscriptionOut(BaseModel):
    id: uuid.UUID
    household_id: uuid.UUID
    plan: PlanCode
    status: SubscriptionStatus
    payment_mode: PaymentMode
    paused_until: date | None
    auto_approve: bool
    started_at: datetime


class SlotRefOut(BaseModel):
    id: uuid.UUID
    label: str
    start: time
    end: time


class DeliveryDayOut(BaseModel):
    weekday: int = Field(ge=0, le=6, description="0 = Monday")
    slot: SlotRefOut


class DeliveryDaysOut(BaseModel):
    items: list[DeliveryDayOut]


class DeliveryDayIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    weekday: int = Field(ge=0, le=6)
    slot_id: uuid.UUID


class DeliveryDaysIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[DeliveryDayIn] = Field(min_length=1, max_length=3)


class CycleStep(BaseModel):
    qty: int
    unit: Unit
    label: str


class ReplacedOut(BaseModel):
    product_id: uuid.UUID
    slug: str
    name: str
    unit_price_paise: int


class CycleLineOut(BaseModel):
    product_id: uuid.UUID
    slug: str
    name: str
    qty: int
    step: CycleStep
    unit_price_paise: int
    total_paise: int
    reason_code: str
    reason_params: dict[str, object] | None
    source: LineSource
    replaces: ReplacedOut | None


class MealOut(BaseModel):
    slot: Slot
    recipe_id: uuid.UUID | None
    name: str | None
    tiffin: bool
    sides: list[str]
    source: Literal["plan", "customer"] = Field(description="customer: set with PUT .../meals")


class DayOut(BaseModel):
    date: date
    meals: list[MealOut]


class MealPlanOut(BaseModel):
    source: Literal["ml", "fallback"]
    days: list[DayOut]


class HighlightOut(BaseModel):
    code: str
    params: dict[str, object]


class BudgetLockOut(BaseModel):
    month: str = Field(examples=["2026-10"])
    budget_paise: int
    spent_paise: int
    remaining_paise: int


class BudgetLockIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$", examples=["2026-10"])
    budget_paise: int | None = Field(
        default=None, ge=50_000, description="At least ₹500; null switches the lock off"
    )


class LineRemoveOp(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["remove"]
    product_id: uuid.UUID


class LineAddOp(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["add"]
    product_id: uuid.UUID
    qty: int = Field(ge=1, le=20, description="Steps")


class LineQtyOp(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["qty"]
    product_id: uuid.UUID
    qty: int = Field(ge=1, le=20, description="Steps")


class LineSubstituteOp(BaseModel):
    model_config = ConfigDict(extra="forbid")

    op: Literal["substitute"]
    product_id: uuid.UUID
    substitute_id: uuid.UUID = Field(description="From GET /v1/products/{id}/substitutes")


LineOp = Annotated[
    LineRemoveOp | LineAddOp | LineQtyOp | LineSubstituteOp, Field(discriminator="op")
]


class LinesPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ops: list[LineOp] = Field(min_length=1, max_length=20, description="Applied in order")


class MealSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: date
    slot: str = Field(
        json_schema_extra={"enum": ["breakfast", "lunch", "dinner"]},
        description="Another value is 422 subscriptions.meal_unknown",
    )
    set: Literal["next", "recipe", "tiffin", "no_cook", "planned"] = Field(
        default="next",
        description="next: the cheapest allowed dish not in the week; recipe: recipe_id; "
        "tiffin, no_cook: no ingredients; planned: back to the planner's dish",
    )
    recipe_id: uuid.UUID | None = Field(default=None, description="Only with set: recipe")

    @model_validator(mode="after")
    def _recipe_only_with_recipe(self) -> "MealSet":
        if (self.set == "recipe") != (self.recipe_id is not None):
            raise ValueError("recipe_id goes with set: recipe, and only with it")
        return self


class FeedbackItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: uuid.UUID
    signal: Literal["ran_out", "wasted", "just_right"]


class FeedbackIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[FeedbackItem] = Field(min_length=1, max_length=50)


class FeedbackOut(BaseModel):
    recorded: int


class CaptainOut(BaseModel):
    name: str
    flat: str


class PaymentDueOut(BaseModel):
    """What a `payment_failed` week still needs (§3.3); paid with POST /v1/orders/{id}/pay."""

    order_id: uuid.UUID
    total_paise: int
    wallet_paise: int = Field(description="The wallet's balance now")
    shortfall_paise: int = Field(description="What a top-up must add: total minus wallet, >= 0")
    due_at: datetime = Field(description="The end of the grace period (T-1 23:00 IST), in UTC")


class CycleOut(BaseModel):
    id: uuid.UUID | None = Field(description="null for a preview")
    household_id: uuid.UUID
    week_start: date
    delivery_date: date
    slot: SlotRefOut
    cutoff_at: datetime
    status: CycleStatus
    version: int
    meal_plan: MealPlanOut
    lines: list[CycleLineOut]
    total_paise: int
    highlights: list[HighlightOut]
    budget_lock: BudgetLockOut | None
    captain: CaptainOut | None
    order_id: uuid.UUID | None
    generated_at: datetime | None
    approved_at: datetime | None
    payment_due: PaymentDueOut | None = Field(description="Only while the week is payment_failed")
