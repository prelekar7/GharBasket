"""Request and response models of M9 Community & Kindness.

The society shapes are the frozen S1 contract (docs/modules/M9-community.md §3.1): changing them
needs a PR reviewed by both app owners. Captains are women living in the society, so no shape here
ever carries a captain's flat number or phone.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

SocietyStatus = Literal["prospect", "unlocking", "live", "paused"]

# India's bounding box: a pin outside it is a mistake (or the 0,0 of an unset map).
LAT_RANGE = (6.0, 37.0)
LNG_RANGE = (68.0, 98.0)


class LatLng(BaseModel):
    lat: float = Field(ge=LAT_RANGE[0], le=LAT_RANGE[1], examples=[18.99])
    lng: float = Field(ge=LNG_RANGE[0], le=LNG_RANGE[1], examples=[73.11])


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
