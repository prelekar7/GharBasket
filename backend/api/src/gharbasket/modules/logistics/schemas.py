"""Request and response models of M7 Logistics.

The vehicle shape is the frozen S5 contract (docs/modules/M7-logistics.md §3.1): changing it needs
a PR reviewed by both app owners. Whether a vehicle can carry its kind's load
(`logistics.capacity_invalid`) and whether the default rider rides for the hub
(`logistics.rider_invalid`) are checked by the service, so clients get those codes.
"""

import uuid
from datetime import date, datetime
from typing import ClassVar, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
PlanSource = Literal["ml", "fallback_previous", "fallback_nearest"]
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
    km: float = Field(examples=[14.8])
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
    source: PlanSource | None = Field(description="Null until solved")
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
