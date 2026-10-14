"""Request and response models of M7 Logistics.

The vehicle shape is the frozen S5 contract (docs/modules/M7-logistics.md §3.1): changing it needs
a PR reviewed by both app owners. Whether a vehicle can carry its kind's load
(`logistics.capacity_invalid`) and whether the default rider rides for the hub
(`logistics.rider_invalid`) are checked by the service, so clients get those codes.
"""

import uuid
from datetime import datetime
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
