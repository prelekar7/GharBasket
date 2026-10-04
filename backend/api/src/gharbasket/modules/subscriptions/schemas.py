"""Request and response models of M3 Plans & Subscriptions: the S3 contract (spec §3.1)."""

import uuid
from datetime import date, datetime, time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PlanCode = Literal["autopilot", "tiffin", "salad"]
PaymentMode = Literal["wallet", "autopay"]
SubscriptionStatus = Literal["active", "paused", "ended"]
CycleStatus = Literal[
    "draft", "awaiting_approval", "approved", "skipped", "cancelled", "locked", "preview"
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


class CaptainOut(BaseModel):
    name: str
    flat: str


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
