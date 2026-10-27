"""Request and response models of M9 Community & Kindness.

The society shapes are the frozen S1 contract (docs/modules/M9-community.md §3.1): changing them
needs a PR reviewed by both app owners. Captains are women living in the society, so no shape here
ever carries a captain's flat number or phone.
"""

import uuid
from datetime import date, datetime
from itertools import chain, count
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from gharbasket.platform.geo import GeoPolygon, LatLng

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


def _tidy(value: str | None) -> str | None:
    if value is None:
        return None
    tidy = " ".join(value.split())
    return tidy or None


class TowerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9][a-z0-9-]{0,15}$",
        description="Unique in the society; left out, the next free `a`, `b`, `c`... is used",
        examples=["a"],
    )
    label: str = Field(min_length=1, max_length=60, examples=["A wing"])

    @field_validator("label")
    @classmethod
    def _tidy_label(cls, value: str) -> str:
        tidy = _tidy(value)
        if tidy is None:
            raise ValueError("label needs a character")
        return tidy


def fill_tower_ids(towers: list[TowerIn]) -> None:
    """Give the towers without an id the next free `a`, `b`, `c`...; given ids must be unique."""
    given = [t.id for t in towers if t.id is not None]
    if len(given) != len(set(given)):
        raise ValueError("tower ids must be unique")
    # Past `z` (a society has at most 50 towers): t27, t28...
    names = chain("abcdefghijklmnopqrstuvwxyz", (f"t{n}" for n in count(27)))
    free = (name for name in names if name not in given)
    for tower in towers:
        if tower.id is None:
            tower.id = next(free)


class SocietyUnlockIn(BaseModel):
    """The hub's PATCH (§3.6, §3.9): unlock settings, boundary and towers; every field optional.
    `boundary: null` clears the boundary; `towers` replaces the whole list."""

    model_config = ConfigDict(extra="forbid")

    unlock_threshold: int | None = Field(default=None, ge=1, le=500, examples=[25])
    unlock_threshold_pinned: bool | None = Field(
        default=None, description="Pinned: the weekly recompute leaves the threshold alone"
    )
    status: Literal["unlocking", "paused", "live"] | None = Field(
        default=None, description="A hub decision, audit-logged"
    )
    boundary: GeoPolygon | None = Field(
        default=None,
        description="The drawn boundary; null clears it. Checked like a hub's service area",
    )
    towers: list[TowerIn] | None = Field(
        default=None,
        max_length=50,
        description="Replaces the whole list; ids left out get the next free letter",
    )

    @model_validator(mode="after")
    def _towers(self) -> "SocietyUnlockIn":
        if "towers" in self.model_fields_set:
            if self.towers is None:
                raise ValueError("towers cannot be null; send [] to remove them all")
            fill_tower_ids(self.towers)
        return self


class SocietyUnlockAdminOut(UnlockOut):
    unlock_threshold_pinned: bool


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


ReferralStatus = Literal["joined", "converted", "void"]


class ReferralRewards(BaseModel):
    referrer_points: int = Field(examples=[250])
    referee_points: int = Field(examples=[250])
    monthly_cap: int = Field(examples=[10], description="Rewarded conversions per IST month")


class ReferralSummary(BaseModel):
    joined: int = Field(examples=[3])
    converted: int = Field(examples=[2])
    points_earned: int = Field(examples=[500])


class ReferredBy(BaseModel):
    status: ReferralStatus
    points: int | None = Field(examples=[250], description="Set once converted")


class ReferralItem(BaseModel):
    """A neighbour who joined with the caller's code: never a name, flat, tower or phone."""

    id: uuid.UUID
    status: ReferralStatus
    joined_on: date
    converted_on: date | None
    points: int | None = Field(description="Set once converted; 0 over the monthly cap")


class ReferralsOut(BaseModel):
    code: str = Field(examples=["K7Q2XD"])
    link: str | None = Field(
        examples=["https://gharbasket.in/s/sunrise-towers-kharghar?ref=K7Q2XD"],
        description="null while the caller has no household",
    )
    share_text: str = Field(description="In the request language; carries the link")
    rewards: ReferralRewards
    summary: ReferralSummary
    referred_by: ReferredBy | None
    items: list[ReferralItem] = Field(description="Newest first, at most 50")


class ReferralClaimIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=16, examples=["K7Q2XD"])


class ReferralClaimOut(BaseModel):
    id: uuid.UUID
    status: Literal["joined"]
    referee_points: int = Field(examples=[250])
    converts_on: Literal["first_delivered_box"]


# --- the staff society list and society request review (§3.9) -------------------------------------


class HubRef(BaseModel):
    id: uuid.UUID
    name: str | None = Field(examples=["Kharghar hub"])


class AdminSociety(BaseModel):
    """A society as hub staff and admins see it: never a captain or a requester."""

    id: uuid.UUID
    slug: str
    name: str
    area: str | None
    status: SocietyStatus
    hub: HubRef | None = Field(description="null: no hub yet (admins only)")
    households: int = Field(description="Households whose address is in it (the unlock count)")
    unlock_threshold: int
    unlock_threshold_pinned: bool
    threshold_source: Literal["model", "hub", "default"]
    live_at: datetime | None
    flats: int | None
    towers: list[Tower]
    boundary: GeoPolygon | None
    created_at: datetime
    updated_at: datetime


class LatLngOut(BaseModel):
    lat: float
    lng: float


class SocietyRequestAdmin(BaseModel):
    """A society request in the review queue: never the requester's id, name or phone."""

    id: uuid.UUID
    name: str
    area: str | None
    location: LatLngOut | None
    note: str | None
    status: Literal["received", "accepted", "duplicate", "rejected"]
    society_id: uuid.UUID | None = Field(description="The society it became (accepted)")
    duplicate_of: uuid.UUID | None = Field(description="The society it repeats (duplicate)")
    created_at: datetime
    reviewed_at: datetime | None


class SocietyCreateIn(BaseModel):
    """A new society by hand (§3.1, §3.9): always a `prospect`; the boundary is drawn after, by
    the map editor."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=120, examples=["Panvel Heights"])
    area: str | None = Field(default=None, max_length=120, examples=["New Panvel"])
    hub_id: uuid.UUID | None = Field(
        default=None,
        description="Default: the caller's hub for a hub manager, none for an admin",
    )
    unlock_threshold: int | None = Field(
        default=None, ge=1, le=500, description="Default 20; given, its source is `hub`"
    )
    flats: int | None = Field(default=None, ge=1, le=5000)
    towers: list[TowerIn] = Field(default_factory=list, max_length=50)

    @field_validator("name")
    @classmethod
    def _tidy_name(cls, value: str) -> str:
        tidy = _tidy(value)
        if tidy is None or len(tidy) < 2:
            raise ValueError("name needs at least 2 characters")
        return tidy

    @field_validator("area")
    @classmethod
    def _tidy_area(cls, value: str | None) -> str | None:
        return _tidy(value)

    @model_validator(mode="after")
    def _tower_ids(self) -> "SocietyCreateIn":
        fill_tower_ids(self.towers)
        return self


class SocietyRequestAcceptIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hub_id: uuid.UUID | None = Field(
        default=None,
        description="Default: the caller's hub for a hub manager, none for an admin",
    )


class SocietyRequestDuplicateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    society_id: uuid.UUID | None = Field(
        default=None, description="Any listed society; missing or unknown is 422"
    )


class SocietyRequestUndoIn(BaseModel):
    """The undo of "Duplicate": `{ "status": "received" }` and nothing else."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["received"]


class SocietyRequestAccepted(BaseModel):
    request: SocietyRequestAdmin
    society: AdminSociety
