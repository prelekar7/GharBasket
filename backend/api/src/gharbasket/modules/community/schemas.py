"""Request and response models of M9 Community & Kindness.

The society shapes are the frozen S1 contract (docs/modules/M9-community.md §3.1): changing them
needs a PR reviewed by both app owners. Captains are women living in the society, so no shape here
ever carries a captain's flat number or phone.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from gharbasket.platform.geo import LatLng

SocietyStatus = Literal["prospect", "unlocking", "live", "paused"]


class SocietySummary(BaseModel):
    """A search result. Never carries captain data, not even for residents."""

    id: uuid.UUID
    slug: str = Field(examples=["sunrise-towers-kharghar"])
    name: str = Field(examples=["Sunrise Towers"])
    area: str | None = Field(examples=["Sector 20, Kharghar"])
    status: SocietyStatus
    distance_m: int | None = Field(
        description="Metres from `near` to the society's boundary; null without `near`",
        examples=[400],
    )


class Tower(BaseModel):
    id: str = Field(examples=["a"])
    label: str = Field(examples=["A wing"])


class CaptainCard(BaseModel):
    display_name: str = Field(examples=["Meena tai"])
    photo_url: str | None


class SocietyOut(SocietySummary):
    towers: list[Tower]
    flats: int | None = Field(examples=[240])
    captain: CaptainCard | None = Field(
        description="Only for residents of this society with an active captain (M8); else null"
    )


class UnlockOut(BaseModel):
    """How close a society is to free delivery (US-2)."""

    society_id: uuid.UUID
    status: SocietyStatus
    joined: int = Field(examples=[14])
    threshold: int = Field(examples=[20])
    live_at: datetime | None = Field(description="Set once, when it first went live")
    threshold_source: Literal["model", "hub", "default"]
    box_fee_paise: int = Field(
        examples=[3000],
        description="The delivery fee on each box until live; 0 once `live_at` is set",
    )


class SocietyUnlockIn(BaseModel):
    """The hub's unlock settings (§3.6); every field optional."""

    model_config = ConfigDict(extra="forbid")

    unlock_threshold: int | None = Field(default=None, ge=1, le=500, examples=[25])
    unlock_threshold_pinned: bool | None = Field(
        default=None, description="Pinned: the weekly recompute leaves the threshold alone"
    )
    status: Literal["unlocking", "paused", "live"] | None = Field(
        default=None, description="A hub decision, audit-logged"
    )


class SocietyUnlockAdminOut(UnlockOut):
    unlock_threshold_pinned: bool


def _tidy(value: str | None) -> str | None:
    if value is None:
        return None
    tidy = " ".join(value.split())
    return tidy or None


class SocietyRequestIn(BaseModel):
    """Ask GharBasket to come to a society that isn't listed (US-8)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=120, examples=["Panvel Heights"])
    area: str | None = Field(default=None, max_length=120, examples=["New Panvel"])
    location: LatLng | None = Field(
        default=None, description="null when no pin was placed; never a default map centre"
    )
    note: str | None = Field(default=None, max_length=500)

    @field_validator("name")
    @classmethod
    def _tidy_name(cls, value: str) -> str:
        tidy = _tidy(value)
        if tidy is None or len(tidy) < 2:
            raise ValueError("name needs at least 2 characters")
        return tidy

    @field_validator("area", "note")
    @classmethod
    def _tidy_optional(cls, value: str | None) -> str | None:
        return _tidy(value)


class SocietyRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: Literal["received", "accepted", "rejected", "duplicate"]
    created_at: datetime


class CaptainOut(BaseModel):
    """An appointment as hub staff see it (§3.2): never the captain's flat or phone."""

    id: uuid.UUID
    society_id: uuid.UUID
    hub_id: uuid.UUID
    user_id: uuid.UUID
    partner_id: uuid.UUID
    display_name: str | None = Field(examples=["Meena tai"], description="From M8")
    photo_url: str | None = Field(description="null for now (M8 §11)")
    status: Literal["active", "ended"]
    since: datetime
    until: datetime | None
    appointed_by: Literal["auto", "hub"] = Field(description="auto: on partners.verified")
    end_reason: Literal["stepped_down", "replaced", "hub_decision", "suspended"] | None


class CaptainsOut(BaseModel):
    items: list[CaptainOut]


class CaptainIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    partner_id: uuid.UUID
    replace: bool = Field(
        default=False, description="End the current captain (replaced) and appoint this one"
    )


class CaptainEndIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Literal["stepped_down", "hub_decision"]
    note: str | None = Field(default=None, max_length=300)

    @field_validator("note")
    @classmethod
    def _tidy_note(cls, value: str | None) -> str | None:
        return _tidy(value)
