"""Request and response models of M4 Orders & Checkout: the S2 contract (spec §3.1, §3.2) and
the issue triage (§3.6)."""

import uuid
from datetime import date, datetime, time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
Actor = Literal["customer", "system", "hub", "packer", "rider", "captain", "kitchen"]


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
    actor: Actor = Field(description="Who moved it, as a kind of actor only: never a name or id")


class TimelineEvent(TimelineEntry):
    """A timeline row of the live timeline (§3.3's TimelineEntry): with the row's id."""

    id: uuid.UUID = Field(description="The order_events row; also the SSE event id")


class OrderEventsOut(BaseModel):
    """The JSON form of `GET /v1/orders/{id}/events`, for clients that poll (§3.3)."""

    items: list[TimelineEvent] = Field(description="Oldest first")
    status: Status
    version: int
    eta: None = Field(None, description="null until M7 publishes route progress")


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
IssueStatus = Literal["open", "credited", "rejected"]


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
    status: IssueStatus
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


# --- triage (§3.6) ------------------------------------------------------------------------------


class IssueTriage(IssueOut):
    household_id: uuid.UUID
    hub_id: uuid.UUID
    society_id: uuid.UUID
    delivery_date: date
    order_total_paise: int = Field(examples=[69000])
    creditable_paise: int = Field(
        description="What is left of the order's paid total after shortage refunds and credits"
    )
    max_credit_paise: int = Field(
        description="What resolve would accept now: amount_paise, or the order total for late "
        "and other, capped by creditable_paise; 0 once resolved"
    )
    auto_credited_7d_paise: int = Field(
        description="The household's automatic credits in the last 7 days"
    )
    callback_due_at: datetime | None = Field(
        description="created_at + callback_within_minutes, while open"
    )


class IssuePhoto(BaseModel):
    url: str = Field(description="A 5-minute signed GET")
    expires_at: datetime


class IssueContact(BaseModel):
    name: str | None
    phone_e164: str


class IssueTriageDetail(IssueTriage):
    photos: list[IssuePhoto] = Field(
        description="Empty while storage is unavailable; photo_count says how many there are"
    )
    contact: IssueContact | None = Field(description="The household owner, to call back")


class ResolveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: Literal["credit", "reject"]
    credit_paise: int | None = Field(
        default=None, ge=0, description="Credit only; at most max_credit_paise", examples=[4000]
    )
    note: str | None = Field(default=None, max_length=500, description="Required to reject")

    @model_validator(mode="after")
    def _note_to_reject(self) -> "ResolveIn":
        if self.outcome == "reject" and not (self.note and self.note.strip()):
            raise ValueError("A rejection needs a note.")
        if self.outcome == "reject" and self.credit_paise:
            raise ValueError("A rejection credits nothing.")
        return self
