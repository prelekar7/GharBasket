"""Request and response models of M6 Hub Operations.

The hub and vendor shapes are the frozen S4 contract (docs/modules/M6-hub-ops.md §3.1): changing
them needs a PR reviewed by both app owners. Service areas and timings are checked by the service
(`hub_ops.area_invalid`, `hub_ops.timings_invalid`), not here, so clients get those codes.
"""

import uuid
from datetime import date, datetime
from typing import ClassVar, Literal, Self

import phonenumbers
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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


# --- packing (§3.2) ---------------------------------------------------------------------------

TaskStatus = Literal["queued", "picking", "packed"]


class PackingSlot(BaseModel):
    id: uuid.UUID
    label: str = Field(examples=["7-9 am"])
    start: str = Field(examples=["07:00"], description="IST HH:MM")
    end: str = Field(examples=["09:00"])


class PackingSociety(BaseModel):
    id: uuid.UUID
    name: str = Field(examples=["Sunrise Towers"])


class PackingRoute(BaseModel):
    route_id: uuid.UUID
    stop_seq: int = Field(examples=[3])


class Packer(BaseModel):
    user_id: uuid.UUID
    name: str | None


class PackingStep(BaseModel):
    qty: int = Field(examples=[250])
    unit: str = Field(examples=["g"])
    label: str = Field(examples=["250 g"], description="In the requested language")


class PackingTaskOut(BaseModel):
    id: uuid.UUID
    hub_id: uuid.UUID
    order_id: uuid.UUID
    order_kind: Literal["box", "addon"]
    delivery_date: date
    slot: PackingSlot | None
    society: PackingSociety
    flat_label: str | None = Field(
        examples=["B wing, B-402"], description="Tower label and flat; no name or phone"
    )
    seq: int = Field(description="Position in the hub's queue for the date")
    route: PackingRoute | None = Field(description="Set when M7 publishes routes")
    status: TaskStatus
    packer: Packer | None = Field(description="Who holds the task")
    bag_qr: str = Field(examples=["GB1:0199a0000000700080000000000000ab"])
    bags: int
    crate_no: int | None = Field(description="Crate within the society, set at complete")
    lines_total: int
    lines_done: int
    started_at: datetime | None
    packed_at: datetime | None
    version: int


class PackingSubstitute(BaseModel):
    product_id: uuid.UUID
    slug: str
    name: str
    step: PackingStep


class PackingLineOut(BaseModel):
    product_id: uuid.UUID = Field(description="The ordered product")
    slug: str
    name: str
    step: PackingStep
    qty: int = Field(description="Steps ordered")
    picked_qty: int = Field(description="Steps in the bag (of the substitute, if any)")
    short_qty: int = Field(description="Steps refunded")
    substitute: PackingSubstitute | None
    done: bool = Field(description="picked_qty + short_qty = qty")


class PackingTaskDetail(PackingTaskOut):
    lines: list[PackingLineOut] = Field(description="By catalog category, then name")


class SocietyLoad(BaseModel):
    society_id: uuid.UUID
    name: str
    bags: int
    packed_bags: int
    crates: int


class PackingSummary(BaseModel):
    date: date
    queued: int
    picking: int
    packed: int
    societies: list[SocietyLoad]


class BuildIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: date


class BuildOut(BaseModel):
    created: int
    existing: int
