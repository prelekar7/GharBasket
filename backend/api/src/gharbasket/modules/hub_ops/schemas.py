"""Request and response models of M6 Hub Operations.

The hub and vendor shapes (§3.1) and the procurement shapes (§3.3) are frozen contracts
(docs/modules/M6-hub-ops.md): changing them needs a PR reviewed by both app owners. Service
areas and timings are checked by the service (`hub_ops.area_invalid`,
`hub_ops.timings_invalid`), not here, so clients get those codes.
"""

import uuid
from datetime import date, datetime
from typing import ClassVar, Literal, Self

import phonenumbers
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from gharbasket.platform.geo import LatLng

HubStatus = Literal["active", "paused"]
VendorKind = Literal["apmc_trader", "fpo", "farmer", "wholesaler"]
SLUG_PATTERN = r"^[a-z0-9-]{3,40}$"


def _tidy(value: object) -> object:
    """Collapse whitespace before the length rules run; blank text becomes null."""
    if not isinstance(value, str):
        return value
    return " ".join(value.split()) or None


class GeoPolygon(BaseModel):
    """GeoJSON, WGS 84, `[lng, lat]`; one closed ring (first point = last), 4 to 500 points, no
    holes, not self-intersecting."""

    type: Literal["Polygon"]
    coordinates: list[list[list[float]]] = Field(
        examples=[[[[73.02, 19.0], [73.13, 19.0], [73.13, 19.05], [73.02, 19.05], [73.02, 19.0]]]]
    )


class _Patch(BaseModel):
    """A PATCH body: only the fields sent change, and only `NULLABLE` ones may be sent as null."""

    model_config = ConfigDict(extra="forbid")
    NULLABLE: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="after")
    def _no_null_for_required(self) -> Self:
        for name in self.model_fields_set - self.NULLABLE:
            if getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class Timings(BaseModel):
    """The hub's day as IST `HH:MM`, in this order; display and job planning only."""

    model_config = ConfigDict(extra="forbid")

    receiving: str = Field(examples=["04:00"])
    packing: str = Field(examples=["05:00"])
    dispatch: str = Field(examples=["06:30"])
    surplus: str = Field(examples=["12:30"])


class TimingsPatch(_Patch):
    """Any of the four; the others keep their current (or default) value."""

    receiving: str | None = None
    packing: str | None = None
    dispatch: str | None = None
    surplus: str | None = None


class HubSummary(BaseModel):
    id: uuid.UUID
    slug: str = Field(examples=["kharghar"])
    name: str = Field(examples=["Kharghar hub"])
    status: HubStatus


class HubOut(HubSummary):
    address: str
    location: LatLng
    service_area: GeoPolygon | None = Field(description="null until an admin draws it")
    timings: Timings
    procurement_buffer_pct: int = Field(examples=[10])
    version: int
    created_at: datetime
    updated_at: datetime


class HubsOut(BaseModel):
    items: list[HubSummary]


class _HubFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    _tidy_text = field_validator("name", "address", mode="before", check_fields=False)(_tidy)


class HubIn(_HubFields):
    slug: str = Field(pattern=SLUG_PATTERN, examples=["kharghar"], description="Never changes")
    name: str = Field(min_length=3, max_length=80, examples=["Kharghar hub"])
    address: str = Field(min_length=1, max_length=300)
    location: LatLng
    service_area: GeoPolygon | None = None
    timings: TimingsPatch | None = Field(default=None, description="Missing times: the defaults")
    procurement_buffer_pct: int = Field(default=10, ge=0, le=50)


class HubPatch(_HubFields, _Patch):
    """Only the fields sent change; `service_area: null` clears the area."""

    NULLABLE: ClassVar[frozenset[str]] = frozenset({"service_area"})

    name: str | None = Field(default=None, min_length=3, max_length=80)
    address: str | None = Field(default=None, min_length=1, max_length=300)
    location: LatLng | None = None
    service_area: GeoPolygon | None = None
    status: HubStatus | None = None
    timings: TimingsPatch | None = None
    procurement_buffer_pct: int | None = Field(default=None, ge=0, le=50)


class VendorOut(BaseModel):
    id: uuid.UUID
    hub_id: uuid.UUID
    name: str = Field(examples=["Shinde Traders"])
    kind: VendorKind
    phone: str | None = Field(examples=["+919800002001"], description="E.164")
    whatsapp: bool = Field(description="Purchase orders can be shared on WhatsApp")
    is_default: bool = Field(description="Suggested for products with no purchase history")
    notes: str | None
    active: bool
    score: float | None = Field(description="Last weekly score; null until scored")
    version: int
    created_at: datetime


class VendorsOut(BaseModel):
    items: list[VendorOut]


class _VendorFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    _tidy_text = field_validator("name", "notes", mode="before", check_fields=False)(_tidy)

    @field_validator("phone", check_fields=False)
    @classmethod
    def _e164(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            number = phonenumbers.parse(value, "IN")
        except phonenumbers.NumberParseException as exc:
            raise ValueError("not a phone number") from exc
        if not phonenumbers.is_valid_number(number):
            raise ValueError("not a valid phone number")
        return phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)


class VendorIn(_VendorFields):
    name: str = Field(min_length=2, max_length=80, examples=["Shinde Traders"])
    kind: VendorKind
    phone: str | None = Field(default=None, max_length=20, examples=["+919800002001"])
    whatsapp: bool = True
    is_default: bool = Field(default=False, description="true clears it on the hub's other vendor")
    notes: str | None = Field(default=None, max_length=500)


class VendorPatch(_VendorFields, _Patch):
    """Only the fields sent change; `phone` and `notes` may be null. Deactivating clears
    `is_default`."""

    NULLABLE: ClassVar[frozenset[str]] = frozenset({"phone", "notes"})

    name: str | None = Field(default=None, min_length=2, max_length=80)
    kind: VendorKind | None = None
    phone: str | None = Field(default=None, max_length=20)
    whatsapp: bool | None = None
    is_default: bool | None = None
    notes: str | None = Field(default=None, max_length=500)
    active: bool | None = None


# --- procurement (§3.3) --------------------------------------------------------------------------

OpsUnit = Literal["kg", "l", "piece", "bunch", "pack"]


class Ref(BaseModel):
    id: uuid.UUID
    name: str


class LinePurchaseOrder(BaseModel):
    id: uuid.UUID
    number: str | None = Field(examples=["KHARGHAR-261215-02"])
    status: str


class ProcurementLineOut(BaseModel):
    product_id: uuid.UUID
    slug: str
    name: str = Field(description="In Accept-Language")
    unit: OpsUnit
    shelf_life_h: int | None
    committed_qty: float = Field(examples=[42.5], description="Paid demand for the date")
    at_risk_qty: float = Field(description="payment_failed orders in the grace; shown, not bought")
    forecast_q50: float | None
    forecast_qtau: float | None
    basis: Literal["forecast", "buffer"] = Field(description="Which rule made buffer_qty")
    buffer_qty: float
    on_hand_qty: float = Field(description="Usable stock for the date (§3.4)")
    suggested_qty: float = Field(description="committed + buffer - on hand, rounded up")
    order_qty: float = Field(description="Suggested, or the hub manager's edit")
    edited: bool
    edit_reason: str | None
    vendor: Ref | None
    unit_cost_paise: int | None = Field(description="Expected cost per operations unit")
    amount_paise: int | None = Field(description="order_qty x unit_cost_paise, rounded half up")
    purchase_order: LinePurchaseOrder | None = Field(description="The line's PO (not cancelled)")
    note: str | None


class PlanTotals(BaseModel):
    lines: int
    order_paise: int = Field(description="Priced lines only")
    unpriced_lines: int


class ProcurementPlanOut(BaseModel):
    id: uuid.UUID
    hub_id: uuid.UUID
    delivery_date: date
    status: Literal["draft", "ordered", "closed"]
    source: Literal["ml", "fallback"] = Field(description="ml when M10 had a forecast")
    model: str | None
    buffer_pct: int
    totals: PlanTotals
    lines: list[ProcurementLineOut] = Field(description="By shelf, then name")
    built_at: datetime
    refreshed_at: datetime | None
    version: int


class PlanBuildIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    day: date = Field(alias="date", description="IST delivery date, today ... today + 2")


class ProcurementLinePatch(_Patch):
    """Only the fields sent change. `order_qty: null` follows `suggested_qty` again; a `reason`
    is required when `order_qty` changes."""

    NULLABLE: ClassVar[frozenset[str]] = frozenset(
        {"order_qty", "vendor_id", "unit_cost_paise", "note"}
    )

    order_qty: float | None = Field(default=None, allow_inf_nan=False, examples=[50.0])
    vendor_id: uuid.UUID | None = None
    unit_cost_paise: int | None = Field(default=None, ge=0, le=10_000_000)
    note: str | None = Field(default=None, max_length=200)
    reason: str | None = Field(default=None, min_length=3, max_length=200)

    _tidy_text = field_validator("note", "reason", mode="before")(_tidy)


class PurchaseOrderVendor(BaseModel):
    id: uuid.UUID
    name: str
    phone: str | None
    whatsapp: bool


class PurchaseOrderLineOut(BaseModel):
    product_id: uuid.UUID
    slug: str
    name: str
    unit: OpsUnit
    qty: float
    unit_cost_paise: int | None
    amount_paise: int | None
    received_qty: float = Field(description="Sum of the receipts (§3.4)")
    accepted_qty: float


class PurchaseOrderOut(BaseModel):
    id: uuid.UUID
    number: str | None = Field(description="Given when sent: hub slug, YYMMDD, a number per day")
    plan_id: uuid.UUID
    hub_id: uuid.UUID
    delivery_date: date
    vendor: PurchaseOrderVendor
    status: Literal["draft", "sent", "partly_received", "received", "cancelled"]
    lines: list[PurchaseOrderLineOut]
    total_paise: int
    unpriced_lines: int
    share_text: str = Field(description="Plain text for WhatsApp or print")
    whatsapp_url: str | None = Field(description="When the vendor has a phone and WhatsApp")
    stale: bool = Field(description="A draft whose plan changed after it was generated")
    cancel_reason: str | None
    created_at: datetime
    sent_at: datetime | None
    closed_at: datetime | None
    version: int


class GeneratedPurchaseOrders(BaseModel):
    purchase_orders: list[PurchaseOrderOut]


class PurchaseOrdersOut(BaseModel):
    items: list[PurchaseOrderOut]


class CancelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=3, max_length=200)

    _tidy_text = field_validator("reason", mode="before")(_tidy)


# --- receiving and stock (§3.4) ------------------------------------------------------------------

Grade = Literal["A", "B", "C"]
RejectReason = Literal["rotten", "damaged", "undersized", "wrong_item", "other"]


class ReceiptLineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: uuid.UUID
    received_qty: float = Field(allow_inf_nan=False, examples=[52.0], description="> 0")
    accepted_qty: float = Field(
        allow_inf_nan=False, examples=[50.0], description="0 … received; the rest goes back"
    )
    grade: Grade = Field(description="A fresh, B cosmetic faults, C kitchens or donation only")
    reject_reason: RejectReason | None = Field(
        default=None, description="Required when accepted < received"
    )
    unit_cost_paise: int | None = Field(
        default=None, ge=0, le=10_000_000, description="Per operations unit; default the PO line's"
    )
    best_before: AwareDatetime | None = Field(
        default=None, description="Default received_at + the product's shelf life"
    )
    note: str | None = Field(default=None, max_length=200)

    _tidy_text = field_validator("note", mode="before")(_tidy)


class ReceiptIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purchase_order_id: uuid.UUID | None = Field(description="null: an ad hoc purchase")
    vendor_id: uuid.UUID | None = Field(
        default=None, description="Required without a purchase order; with one, the PO's"
    )
    received_at: AwareDatetime | None = Field(
        default=None, description="Default now; at most 24 h back"
    )
    lines: list[ReceiptLineIn] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def _vendor_or_po(self) -> Self:
        if self.purchase_order_id is None and self.vendor_id is None:
            raise ValueError("vendor_id is required without a purchase order")
        return self


class UserRef(BaseModel):
    user_id: uuid.UUID
    name: str | None


class ReceiptLineOut(BaseModel):
    product_id: uuid.UUID
    slug: str
    name: str
    unit: OpsUnit
    received_qty: float
    accepted_qty: float
    rejected_qty: float
    grade: Grade
    reject_reason: RejectReason | None
    unit_cost_paise: int | None
    amount_paise: int | None = Field(description="accepted_qty x unit_cost_paise, half up")
    ordered: bool = Field(description="false: not on the purchase order (or no PO)")
    lot_id: uuid.UUID | None = Field(description="null when nothing was accepted")
    best_before: datetime
    note: str | None


class GoodsReceiptOut(BaseModel):
    id: uuid.UUID
    hub_id: uuid.UUID
    vendor: Ref
    purchase_order: LinePurchaseOrder | None = Field(description="Its status after this receipt")
    received_at: datetime
    received_by: UserRef
    lines: list[ReceiptLineOut]
    total_paise: int
    unpriced_lines: int
    created_at: datetime


class GoodsReceiptsOut(BaseModel):
    items: list[GoodsReceiptOut]
