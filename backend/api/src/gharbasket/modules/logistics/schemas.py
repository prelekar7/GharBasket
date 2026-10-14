"""Request and response models of M7 Logistics.

The vehicle shape is the frozen S5 contract (docs/modules/M7-logistics.md §3.1): changing it needs
a PR reviewed by both app owners. Whether a vehicle can carry its kind's load
(`logistics.capacity_invalid`) and whether the default rider rides for the hub
(`logistics.rider_invalid`) are checked by the service, so clients get those codes.
"""

import uuid
from datetime import date, datetime
from typing import Annotated, ClassVar, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

VehicleKind = Literal["cargo_3w", "two_wheeler"]
REGISTRATION = r"^[A-Z0-9]{4,16}$"


def _tidy(value: object) -> object:
    """Collapse whitespace before the length rules run; blank text becomes null."""
    if not isinstance(value, str):
        return value
    return " ".join(value.split()) or None


def _registration(value: object) -> object:
    """`mh 46 ab-1234` → `MH46AB1234`; blank becomes null."""
    if not isinstance(value, str):
        return value
    return "".join(ch for ch in value.upper() if ch.isalnum()) or None


class RiderRef(BaseModel):
    user_id: uuid.UUID
    name: str | None = Field(examples=["Arjun Pawar"], description="Shown to hub staff only")


class VehicleOut(BaseModel):
    id: uuid.UUID
    hub_id: uuid.UUID
    label: str = Field(examples=["EV-1"], description="Unique per hub, ignoring case")
    kind: VehicleKind = Field(description="cargo_3w carries boxes and add-ons; two_wheeler tiffins")
    registration: str | None = Field(examples=["MH46AB1234"])
    capacity_kg: int = Field(examples=[400])
    capacity_crates: int = Field(examples=[24])
    capacity_tiffins: int = Field(examples=[0])
    default_rider: RiderRef | None = Field(description="Set as the rider of new routes")
    active: bool
    version: int
    created_at: datetime


class VehiclesOut(BaseModel):
    items: list[VehicleOut]


class _VehicleFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    _tidy_label = field_validator("label", mode="before", check_fields=False)(_tidy)
    _tidy_registration = field_validator("registration", mode="before", check_fields=False)(
        _registration
    )


class VehicleIn(_VehicleFields):
    """Capacities left out take the kind's defaults: cargo_3w 400 kg, 24 crates, 0 tiffins;
    two_wheeler 40 kg, 0 crates, 30 tiffins."""

    label: str = Field(min_length=1, max_length=20, examples=["EV-1"])
    kind: VehicleKind
    registration: str | None = Field(default=None, pattern=REGISTRATION, examples=["MH46AB1234"])
    capacity_kg: int | None = Field(default=None, ge=10, le=2000, examples=[400])
    capacity_crates: int | None = Field(default=None, ge=0, le=100, examples=[24])
    capacity_tiffins: int | None = Field(default=None, ge=0, le=100, examples=[0])
    default_rider_user_id: uuid.UUID | None = Field(
        default=None, description="A rider of this hub (an active rider:<hub> role)"
    )


class VehiclePatch(_VehicleFields):
    """Only the fields sent change; `registration` and `default_rider_user_id` may be null.
    A change applies to the next solve, never to a route already published."""

    NULLABLE: ClassVar[frozenset[str]] = frozenset({"registration", "default_rider_user_id"})

    label: str | None = Field(default=None, min_length=1, max_length=20)
    kind: VehicleKind | None = None
    registration: str | None = Field(default=None, pattern=REGISTRATION)
    capacity_kg: int | None = Field(default=None, ge=10, le=2000)
    capacity_crates: int | None = Field(default=None, ge=0, le=100)
    capacity_tiffins: int | None = Field(default=None, ge=0, le=100)
    default_rider_user_id: uuid.UUID | None = None
    active: bool | None = None

    @model_validator(mode="after")
    def _no_null_for_required(self) -> Self:
        for name in self.model_fields_set - self.NULLABLE:
            if getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


# --- route plans (§3.2) ----------------------------------------------------------------------

PlanKind = Literal["box", "tiffin"]
PlanStatus = Literal["queued", "solving", "solved", "published", "failed", "superseded"]
PlanSource = Literal["ml", "ml_fallback", "fallback_previous", "fallback_nearest"]
RouteStatus = Literal["draft", "published", "in_progress", "completed", "cancelled"]


class PlanSummary(BaseModel):
    stops: int = Field(examples=[18], description="Stops with orders, routed or not")
    orders: int = Field(examples=[212])
    vehicles: int = Field(examples=[4], description="Vehicles used")
    routes: int = Field(examples=[6])
    km: float = Field(examples=[61.2])
    cost_paise: int = Field(examples=[412000])
    on_time: float = Field(examples=[1.0], description="Share of stops reached within the window")
    late_min: float = Field(examples=[0.0])
    problems: list[str] = Field(
        examples=[["EV-2 trip 1: over capacity leaving the hub"]],
        description="Empty, or the plan needs a hub manager's review before it is published",
    )


class UserRef(BaseModel):
    user_id: uuid.UUID
    name: str | None = Field(examples=["Vikram Desai"])


class VehicleRef(BaseModel):
    id: uuid.UUID
    label: str = Field(examples=["EV-1"])
    kind: VehicleKind


class RouteSummary(BaseModel):
    id: uuid.UUID
    plan_id: uuid.UUID
    hub_id: uuid.UUID
    delivery_date: date
    kind: PlanKind
    trip: int = Field(examples=[1], description="A vehicle's second trip of the day is 2")
    status: RouteStatus = Field(description="draft while its plan is not published")
    vehicle: VehicleRef
    rider: RiderRef | None = Field(description="Null until a rider is set")
    depart: str = Field(examples=["06:30"], description="IST HH:MM")
    return_by: str = Field(examples=["11:30"], description="IST HH:MM")
    stops_total: int = Field(examples=[6])
    stops_done: int = Field(examples=[0])
    crates: int = Field(examples=[18])
    bags: int = Field(examples=[52])
    km: float = Field(
        examples=[14.8], description="Estimate: the straight line x 1.3 along the stops"
    )
    started_at: datetime | None
    completed_at: datetime | None
    version: int


class RoutePlanSummary(BaseModel):
    id: uuid.UUID
    hub_id: uuid.UUID
    delivery_date: date
    kind: PlanKind
    status: PlanStatus
    trigger: Literal["job", "manual"]
    source: PlanSource | None = Field(
        description="Null until solved. ml: M10's solver; ml_fallback: M10 answered with its own "
        "nearest neighbour; fallback_*: M7 planned it because M10 did not answer"
    )
    distance_source: Literal["osrm", "haversine"] | None = Field(
        description="How the distances were priced"
    )
    model: str | None = Field(examples=["cvrptw-waves-v0"])
    summary: PlanSummary | None
    seconds: float | None = Field(examples=[7.9])
    error: str | None = Field(description="Why a plan failed")
    requested_by: UserRef | None
    created_at: datetime
    published_at: datetime | None


class RoutePlanOut(RoutePlanSummary):
    routes: list[RouteSummary]


class RoutePlansOut(BaseModel):
    items: list[RoutePlanSummary]


class SolveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    delivery_date: date = Field(
        alias="date", examples=["2026-12-29"], description="Today or tomorrow (IST)"
    )
    kind: Literal["box"] = Field(default="box", description="tiffin plans come with M8 (§3.7)")


class PublishIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accept_problems: bool = Field(
        default=False, description="Publish although summary.problems is not empty (audited)"
    )


# --- rider execution (§3.3) ------------------------------------------------------------------

StopStatus = Literal["pending", "arrived", "handed_over", "skipped"]


class RoutesOut(BaseModel):
    items: list[RouteSummary]


class LatLngOut(BaseModel):
    lat: float
    lng: float


class StopSociety(BaseModel):
    id: uuid.UUID
    name: str | None = Field(examples=["Sunrise Towers"])
    location: LatLngOut | None = Field(
        description="Where the van stops (the gate, or a point inside)"
    )


class StopSlot(BaseModel):
    id: uuid.UUID
    label: str = Field(examples=["Morning"])
    start: str = Field(examples=["07:00"], description="IST HH:MM")
    end: str = Field(examples=["09:00"], description="IST HH:MM")


class CaptainOut(BaseModel):
    display_name: str = Field(examples=["Meena J."])
    photo_url: str | None


class StopOut(BaseModel):
    id: uuid.UUID
    seq: int = Field(examples=[1])
    kind: Literal["drop", "bank", "kitchen"]
    society: StopSociety | None
    slot: StopSlot | None
    eta: str = Field(examples=["07:12"], description="Planned, IST HH:MM")
    late_min: int = Field(examples=[0], description="Planned minutes after the window's end")
    eta_now: str | None = Field(
        examples=["07:20"],
        description="After start: the planned ETA plus the route's current delay (no live traffic)",
    )
    status: StopStatus
    crates: int = Field(
        examples=[3], description="Planned; the packed counts once the route starts"
    )
    bags: int = Field(examples=[9])
    redelivery_bags: int = Field(
        examples=[1], description="Redelivery bags the van carries for this stop (§3.4)"
    )
    empties_expected: int = Field(examples=[2], description="§3.6")
    captain: CaptainOut | None = Field(description="Null: no active captain (rider mode)")
    rider_mode: bool = Field(description="The rider does the handover and the doors")
    handover_code: str | None = Field(
        examples=["4821"],
        description="Only to the route's rider and hub staff while the route is in progress",
    )
    navigate_url: str | None = Field(
        examples=["https://www.google.com/maps/dir/?api=1&destination=19.03,73.03"]
    )
    arrived_at: datetime | None
    handed_over_at: datetime | None


class RouteDetail(RouteSummary):
    stops: list[StopOut]
    source: PlanSource | None
    left_behind: list[uuid.UUID] = Field(description="Orders not packed when the route started")


class RoutePatch(BaseModel):
    """Before the route starts. A new rider gets logistics.route_published again."""

    model_config = ConfigDict(extra="forbid")

    rider_user_id: uuid.UUID | None = Field(default=None, description="A rider of this hub")
    vehicle_id: uuid.UUID | None = Field(
        default=None, description="An active vehicle of this hub of the route's kind"
    )

    @model_validator(mode="after")
    def _vehicle_not_null(self) -> Self:
        if "vehicle_id" in self.model_fields_set and self.vehicle_id is None:
            raise ValueError("vehicle_id cannot be null")
        return self


SkipReason = Literal["captain_absent", "no_access", "vehicle_issue", "other"]


class FieldTap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_at: datetime | None = Field(
        default=None, description="When the tap happened on the phone (stored for audit)"
    )


class StartIn(FieldTap):
    leave_behind: bool = Field(
        default=False,
        description="Start although orders are still locked or picking: they stay off the route",
    )


class SkipIn(FieldTap):
    reason: SkipReason
    note: str | None = Field(default=None, max_length=300)


class CompleteIn(FieldTap):
    empties_returned: int | None = Field(default=None, ge=0, le=500, examples=[6])


# --- GPS (§3.3) ---------------------------------------------------------------------------------


class PingIn(BaseModel):
    """One position. A ping that is too old, ahead of the clock, outside India, less accurate
    than 200 m or under 5 s after the previous one is dropped (counted, never an error)."""

    model_config = ConfigDict(extra="forbid")

    at: datetime = Field(examples=["2026-12-29T01:41:15Z"])
    lat: float = Field(ge=-90, le=90, examples=[19.0331])
    lng: float = Field(ge=-180, le=180, examples=[73.0298])
    accuracy_m: int = Field(ge=0, le=10_000, examples=[12])
    speed_mps: float | None = Field(default=None, ge=0, le=100, examples=[6.1])
    heading: int | None = Field(default=None, ge=0, le=359, examples=[90])


class LocationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    route_id: uuid.UUID
    pings: list[PingIn] = Field(min_length=1, max_length=20, description="Oldest first")


class LocationOut(BaseModel):
    accepted: int = Field(examples=[4])
    dropped: int = Field(examples=[1])


# --- handover (§3.4) ----------------------------------------------------------------------------

FlatStatus = Literal["on_the_way", "with_you", "held", "delivered", "undelivered"]
BAG_QR = r"^GB1:[0-9a-f]{32}$"


class StopPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rider_mode: bool = Field(description="True: the rider hands over and does the doors today")


class CaptainFlat(BaseModel):
    order_id: uuid.UUID
    flat_label: str | None = Field(examples=["B wing, 402"], description="Tower and flat only")
    kind: Literal["box", "addon", "tiffin"]
    bags: int = Field(examples=[1])
    redelivery: bool
    status: FlatStatus
    hold_until: str | None = Field(examples=["20:00"])


class FlatsOut(BaseModel):
    items: list[CaptainFlat]


class Expected(BaseModel):
    crates: int
    bags: int
    redelivery_bags: int


class CaptainStop(BaseModel):
    stop_id: uuid.UUID
    route_id: uuid.UUID
    slot: StopSlot | None
    eta_now: str | None
    status: StopStatus
    expected: Expected


class SocietyRef(BaseModel):
    id: uuid.UUID
    name: str | None


class FlatCounts(BaseModel):
    on_the_way: int = 0
    with_you: int = 0
    held: int = 0
    delivered: int = 0
    undelivered: int = 0


class CaptainDay(BaseModel):
    date: date
    society: SocietyRef
    stops: list[CaptainStop]
    flats: list[CaptainFlat] = Field(description="By tower, then flat")
    counts: FlatCounts


class HandoverIn(FieldTap):
    crates: int = Field(ge=0, le=500, examples=[5], description="Counted by the captain")
    bags: int = Field(ge=0, le=2000, examples=[14])
    scanned_bags: list[Annotated[str, StringConstraints(pattern=BAG_QR)]] | None = Field(
        default=None, max_length=2000, description="Needed only when the counts do not match"
    )
    handover_code: str | None = Field(default=None, pattern=r"^\d{4}$", examples=["4821"])
    photo_key: str | None = Field(
        default=None, max_length=200, examples=["delivery_photo/<user_id>/<uuid>.jpg"]
    )
    note: str | None = Field(default=None, max_length=300)


class HandoverOut(BaseModel):
    handover_id: uuid.UUID
    stop: StopOut
    at_society: list[uuid.UUID]
    still_dispatched: list[uuid.UUID]
    mismatch: bool


# --- doors (§3.4) and the customer's card (§3.5) ------------------------------------------------

DeliveryStatus = Literal[
    "scheduled",
    "on_route",
    "with_captain",
    "held",
    "delivered",
    "undelivered",
    "redelivery",
    "refunded",
    "triage",
]


class ProofIn(FieldTap):
    bag_qr: str = Field(
        pattern=BAG_QR,
        examples=["GB1:0199a0000000700080000000000000ab"],
        description="This order's",
    )
    proof: Literal["scan", "otp", "photo"] = Field(
        description="A captain may scan alone; a rider needs otp or photo"
    )
    delivery_code: str | None = Field(default=None, pattern=r"^\d{4}$", description="With otp")
    photo_key: str | None = Field(default=None, max_length=200, description="With photo")


class DeliveryOut(BaseModel):
    order_id: uuid.UUID
    attempt: int = Field(examples=[1])
    delivery_date: date
    society_id: uuid.UUID
    status: DeliveryStatus
    reason: (
        Literal[
            "not_home",
            "refused",
            "wrong_address",
            "damaged",
            "missing",
            "other",
            "returned_to_hub",
            "not_confirmed",
        ]
        | None
    )
    hold_until: str | None = Field(examples=["20:00"])
    proof: Literal["scan", "otp", "photo"] | None
    by: Literal["captain", "rider", "hub", "system"] | None
    at: datetime | None
