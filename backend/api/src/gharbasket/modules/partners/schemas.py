"""Request and response models of M8 Partner Network (spec §3.3, frozen for S6).

No shape here ever carries a KYC number, an address or a phone, except `PartnerStaff`, which only
hub staff of the partner's hub and admins read.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

PartnerKind = Literal["kitchen", "captain", "rider", "packer"]
PartnerStatus = Literal["active", "paused", "suspended", "left"]
SuspendReason = Literal["rating", "hygiene", "compliance", "conduct", "other"]


class RatingOut(BaseModel):
    average: float = Field(examples=[4.6])
    count: int = Field(examples=[23])


class SuspensionOut(BaseModel):
    at: datetime
    reason_code: Literal["rating", "hygiene", "compliance", "conduct", "other", "left"]
    note: str | None = Field(description="Staff-only: null in the partner's own view")


class PartnerOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    kind: PartnerKind
    hub_id: uuid.UUID
    display_name: str = Field(examples=["Meena tai"])
    photo_url: str | None = Field(description="Always null for now (M8 §11)")
    status: PartnerStatus
    rating: RatingOut | None = Field(description="Kitchens with 5 ratings or more; else null")
    verified_at: datetime
    suspension: SuspensionOut | None
    version: int


class PartnersOut(BaseModel):
    items: list[PartnerOut]


class SocietyRef(BaseModel):
    id: uuid.UUID
    name: str


class PartnerStaffOut(PartnerOut):
    """A partner as hub staff of her hub (and admins) see her."""

    name: str | None = Field(description="Her M1 name")
    phone_e164: str | None = Field(examples=["+919812345678"])
    application_id: uuid.UUID | None = Field(description="null for seeded partners")
    requested_society: SocietyRef | None = Field(
        description="Captains: the society on her application; null without one"
    )


def _note(value: str | None) -> str | None:
    if value is None:
        return None
    return " ".join(value.split()) or None


class SuspendIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason_code: SuspendReason
    note: str = Field(min_length=1, max_length=300, description="Staff-only")

    @field_validator("note")
    @classmethod
    def _tidy(cls, value: str) -> str:
        tidy = _note(value)
        if tidy is None:
            raise ValueError("note must not be blank")
        return tidy


class ReinstateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=300, description="Staff-only")

    @field_validator("note")
    @classmethod
    def _tidy(cls, value: str | None) -> str | None:
        return _note(value)
