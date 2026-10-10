"""Request and response models of Kindness point rates and donation drives (M9 §3.4).

The shapes are the contract the mobile Kindness screen and the console's Drives and Point rates
pages build against: changing them needs a PR reviewed by both app owners.
"""

import uuid
from datetime import date
from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

DriveScope = Literal["platform", "hub", "society"]
DriveState = Literal["upcoming", "running", "ended", "cancelled"]


class Label(BaseModel):
    """A short name in each app language."""

    model_config = ConfigDict(extra="forbid")

    en: str = Field(min_length=1, max_length=80, examples=["Diwali drive"])
    hi: str = Field(min_length=1, max_length=80)
    mr: str = Field(min_length=1, max_length=80)


class Texts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    en: str = Field(min_length=1, max_length=500)
    hi: str = Field(min_length=1, max_length=500)
    mr: str = Field(min_length=1, max_length=500)


class PointRateOut(BaseModel):
    code: str = Field(examples=["clothes"])
    label: str = Field(examples=["Clothes"], description="In the request language")
    unit: Literal["kg", "each"]
    points: int = Field(examples=[40], description="Points per kg or per piece")
    max_qty: float = Field(examples=[15], description="The most one donation may declare")


class PointRatesOut(BaseModel):
    items: list[PointRateOut]


class PointRateIn(BaseModel):
    """Applies to donations verified from now on."""

    model_config = ConfigDict(extra="forbid")

    points: int = Field(ge=1, le=1000, examples=[40])
    max_qty: Decimal = Field(
        gt=0, le=100, decimal_places=1, description="Whole pieces, or kg in 0.5 steps"
    )
    label: Label


class DriveNgo(BaseModel):
    id: uuid.UUID
    name: str


class DriveProgress(BaseModel):
    """Items (pieces, and kilograms counted as one item per kg); no households, no names."""

    pledged: int = Field(examples=[40], description="In scheduled and picked-up donations")
    verified: int = Field(examples=[31], description="Counted by the hub")


class DriveOut(BaseModel):
    id: uuid.UUID
    code: str = Field(examples=["diwali-2026"])
    name: str = Field(examples=["Diwali drive"], description="In the request language")
    description: str | None
    scope: DriveScope
    hub_id: uuid.UUID | None = Field(description="Also set for a society drive (its hub)")
    society_id: uuid.UUID | None
    codes: list[str] = Field(examples=[["clothes", "toys"]], description="What it multiplies")
    multiplier: float = Field(examples=[2.0])
    starts_on: date = Field(description="IST, inclusive")
    ends_on: date = Field(description="IST, inclusive")
    state: DriveState = Field(description="From the dates in IST, except cancelled")
    ngo: DriveNgo | None = Field(description="Who receives the items; null until NGOs (§3.5)")
    goal_items: int | None
    progress: DriveProgress
    version: int = Field(description="Send it back in If-Match to change the drive")


class DrivesOut(BaseModel):
    items: list[DriveOut]
    next_cursor: str | None


def _distinct(codes: list[str] | None) -> list[str] | None:
    if codes is not None and len(set(codes)) != len(codes):
        raise ValueError("codes must be distinct")
    return codes


class DriveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(
        min_length=3, max_length=60, pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$", examples=["diwali-2026"]
    )
    name: Label
    description: Texts | None = None
    scope: DriveScope
    hub_id: uuid.UUID | None = Field(default=None, description="Only for a hub drive")
    society_id: uuid.UUID | None = Field(default=None, description="Only for a society drive")
    codes: list[str] = Field(min_length=1, max_length=5, examples=[["clothes", "toys"]])
    multiplier: Decimal = Field(ge=1, le=3, decimal_places=1, examples=[2.0])
    starts_on: date
    ends_on: date
    ngo_id: None = Field(default=None, description="Accepted once NGOs exist (§3.5)")
    goal_items: int | None = Field(default=None, ge=1, le=100_000)

    _codes = field_validator("codes")(_distinct)

    @model_validator(mode="after")
    def _scope_ids(self) -> Self:
        wanted = {"platform": (False, False), "hub": (True, False), "society": (False, True)}
        if (self.hub_id is not None, self.society_id is not None) != wanted[self.scope]:
            raise ValueError("a hub drive takes hub_id, a society drive society_id, else neither")
        return self


class DrivePatch(BaseModel):
    """Any field below, or `state: cancelled`. Scope, hub and society stay as created."""

    model_config = ConfigDict(extra="forbid")

    name: Label | None = None
    description: Texts | None = None
    codes: list[str] | None = Field(default=None, min_length=1, max_length=5)
    multiplier: Decimal | None = Field(default=None, ge=1, le=3, decimal_places=1)
    starts_on: date | None = None
    ends_on: date | None = None
    goal_items: int | None = Field(default=None, ge=1, le=100_000)
    state: Literal["cancelled"] | None = Field(
        default=None, description="Donations already scheduled keep the multiplier"
    )

    _codes = field_validator("codes")(_distinct)

    @model_validator(mode="after")
    def _not_null(self) -> Self:
        for name in ("name", "codes", "multiplier", "starts_on", "ends_on", "state"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self
