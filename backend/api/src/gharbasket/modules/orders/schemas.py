"""Request and response models of M4 Orders & Checkout: the S2 contract (spec §3.1, §3.2)."""

import uuid
from datetime import date, datetime, time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from gharbasket.modules.orders.models import ISSUE_KINDS

Unit = Literal["g", "kg", "ml", "l", "piece", "bunch", "pack"]
SlotKind = Literal["box", "tiffin"]


class OrderStep(BaseModel):
    """The unit a person adds or removes (the same shape as M2's `Step`); prices are per step."""

    qty: int = Field(examples=[250])
    unit: Unit
    label: str = Field(examples=["250 g"], description="In the requested language")


OrderKind = Literal["box", "tiffin", "addon"]
LineKind = Literal["box", "addon"]
Status = Literal[
    "approved",
    "locked",
    "payment_failed",
    "cancelled",
    "picking",
    "packed",
    "dispatched",
    "at_society",
    "delivered",
    "undelivered",
    "redelivery",
    "refunded",
]
Actor = Literal["customer", "system", "hub"]


class SlotOut(BaseModel):
    id: uuid.UUID
    hub_id: uuid.UUID
    label: str
    kind: SlotKind
    start: time = Field(description="IST clock time", examples=["07:00"])
    end: time
    capacity: int
    booked: int = Field(description="For the requested date")
    available: int


class SlotsOut(BaseModel):
    date: date
    items: list[SlotOut]


class SlotIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hub_id: uuid.UUID
    label: str = Field(min_length=1, max_length=40, examples=["Morning 7-9"])
    kind: SlotKind = "box"
    start: time = Field(examples=["07:00"])
    end: time = Field(examples=["09:00"])
    capacity: int = Field(ge=1, le=2000, examples=[120])


class SlotPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    capacity: int | None = Field(default=None, ge=1, le=2000)
    active: bool | None = None


class SlotRef(BaseModel):
    id: uuid.UUID
    label: str
    start: time
    end: time


class ProductRef(BaseModel):
    product_id: uuid.UUID
    slug: str
    name: str


class OrderLineOut(BaseModel):
    product_id: uuid.UUID
    slug: str
    name: str = Field(description="In the Accept-Language language")
    kind: LineKind
    qty: int = Field(description="Steps", examples=[2])
    step: OrderStep
    unit_price_paise: int = Field(
        description="Per step, frozen when the line was made; a packing substitute's price"
    )
    total_paise: int = Field(description="(qty - shortage_qty) x unit_price_paise")
    reason_code: str | None
    reason_params: dict[str, object] | None
    substituted_from: ProductRef | None
    shortage_qty: int


class TimelineEntry(BaseModel):
    status: Status
    at: datetime
    actor: Actor


class OrderSummaryOut(BaseModel):
    id: uuid.UUID
    kind: OrderKind
    status: Status
    cycle_id: uuid.UUID | None
    household_id: uuid.UUID
    hub_id: uuid.UUID
    society_id: uuid.UUID
    delivery_date: date
    slot: SlotRef
    cutoff_at: datetime = Field(description="21:00 IST the day before delivery")
    subtotal_paise: int
    fees_paise: int
    discount_paise: int
    total_paise: int
    version: int
    created_at: datetime
    locked_at: datetime | None


class OrderOut(OrderSummaryOut):
    lines: list[OrderLineOut]
    timeline: list[TimelineEntry]


class AddonItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: uuid.UUID
    qty: int = Field(ge=0, le=20, description="Steps; 0 removes the add-on")


class AddonsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AddonItem] = Field(min_length=1, max_length=20)


# --- issues (§3.2) ------------------------------------------------------------------------------

IssueKind = Literal["missing", "damaged", "quality", "wrong_item", "late", "other"]


class IssueItemIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: uuid.UUID
    qty: int = Field(ge=1, le=100, description="Steps, at most what was delivered", examples=[1])


class IssueIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # A plain string so that an unknown kind is `orders.issue_kind_invalid`, not request.invalid.
    kind: str = Field(max_length=20, json_schema_extra={"enum": list(ISSUE_KINDS)})
    items: list[IssueItemIn] | None = Field(
        default=None,
        max_length=20,
        description="Required for missing, damaged, quality and wrong_item; lines of this order",
    )
    note: str | None = Field(default=None, max_length=500)
    photo_keys: list[str] = Field(
        default_factory=list,
        max_length=3,
        description="Keys from POST /v1/files/presign (purpose order_issue_photo)",
    )


class IssueItemOut(BaseModel):
    product_id: uuid.UUID
    slug: str
    name: str = Field(description="In the Accept-Language language")
    qty: int
    unit_price_paise: int = Field(description="The line's paid price per step")
    amount_paise: int


class IssueResolution(BaseModel):
    by: Literal["auto", "hub"]
    note: str | None
    at: datetime


class IssueOut(BaseModel):
    id: uuid.UUID
    order_id: uuid.UUID
    kind: IssueKind
    status: Literal["open", "credited", "rejected"]
    items: list[IssueItemOut]
    amount_paise: int = Field(
        description="The sum of qty times the line's paid unit price", examples=[4000]
    )
    credit_paise: int = Field(description="0 until credited")
    note: str | None
    photo_count: int
    resolution: IssueResolution | None
    callback_within_minutes: int | None = Field(description="While open: the hub calls back")
    open_reason: (
        Literal[
            "photo_needed", "over_auto_credit_limit", "over_order_total", "wallet_limit", "triage"
        ]
        | None
    )
    created_at: datetime


class IssuesOut(BaseModel):
    items: list[IssueOut]
