"""Request and response models of NGOs, surplus offers to NGOs and hand-offs (M9 §3.5).

The shapes are the contract the console's NGOs, NGO offers and Hand-offs pages (and the NGO
coordinator's console) build against: changing them needs a PR reviewed by both app owners.
No shape here carries a donor's household, flat or name.
"""

import uuid
from datetime import date, datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from gharbasket.modules.community.kindness_schemas import PhotoUrl

NgoKind = Literal["food_bank", "shelter", "school", "other"]
NgoStatus = Literal["active", "paused"]
Gift = Literal["items", "food", "meals"]
OfferStatus = Literal["open", "claimed", "expired"]


def _distinct[T](values: list[T] | None) -> list[T] | None:
    if values is not None and len(set(values)) != len(values):
        raise ValueError("values must be distinct")
    return values


class Coordinator(BaseModel):
    name: str | None
    phone_e164: str


class NgoOut(BaseModel):
    id: uuid.UUID
    name: str = Field(examples=["Annapurna Food Bank"])
    kind: NgoKind
    hub_ids: list[uuid.UUID] = Field(description="Hubs it collects from")
    accepts: list[Gift]
    registration_ref: str | None = Field(examples=["DARPAN MH/2019/0123456"])
    verified: bool
    status: NgoStatus
    note: str | None
    coordinator: Coordinator | None = Field(
        description="The first active ngo_coordinator (an M1 role grant); staff and the NGO only"
    )
    version: int = Field(description="Send it back in If-Match to change the NGO")


class NgosOut(BaseModel):
    items: list[NgoOut]


class NgoIn(BaseModel):
    """Coordinators are M1 role grants (`ngo_coordinator:<id>`, POST /v1/admin/user-roles)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120, examples=["Annapurna Food Bank"])
    kind: NgoKind
    hub_ids: list[uuid.UUID] = Field(min_length=1, max_length=20)
    accepts: list[Gift] = Field(min_length=1, max_length=3)
    registration_ref: str | None = Field(default=None, max_length=60)
    verified: bool = False
    status: NgoStatus = "active"
    note: str | None = Field(default=None, max_length=300)

    _hubs = field_validator("hub_ids")(_distinct)
    _accepts = field_validator("accepts")(_distinct)


class NgoPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    kind: NgoKind | None = None
    hub_ids: list[uuid.UUID] | None = Field(default=None, min_length=1, max_length=20)
    accepts: list[Gift] | None = Field(default=None, min_length=1, max_length=3)
    registration_ref: str | None = Field(default=None, max_length=60)
    verified: bool | None = None
    status: NgoStatus | None = None
    note: str | None = Field(default=None, max_length=300)

    _hubs = field_validator("hub_ids")(_distinct)
    _accepts = field_validator("accepts")(_distinct)

    @model_validator(mode="after")
    def _not_null(self) -> Self:
        for name in ("name", "kind", "hub_ids", "accepts", "verified", "status"):
            if name in self.model_fields_set and getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class NgoCard(BaseModel):
    """All anyone outside staff and the NGO ever sees of an NGO."""

    id: uuid.UUID
    name: str


class OfferProduct(BaseModel):
    id: uuid.UUID
    name: str = Field(description="In the request language")


class NgoOfferOut(BaseModel):
    offer_id: uuid.UUID = Field(description="M6's surplus offer id")
    hub_id: uuid.UUID
    product: OfferProduct
    qty: float = Field(examples=[12.5])
    unit: str = Field(examples=["kg"], description="kg, l, piece, bunch or pack")
    kg: float | None = Field(examples=[12.5], description="Null when the weight is unknown")
    expires_at: datetime
    status: OfferStatus = Field(description="An open offer past expires_at is expired")
    claimed_by: NgoCard | None


class NgoOffersOut(BaseModel):
    items: list[NgoOfferOut]


class ClaimIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ngo_id: uuid.UUID = Field(description="One of your NGOs")


class HandoffSummary(BaseModel):
    items: int = Field(description="Pieces, plus kilograms counted as one item per kg")
    kg: float
    meals: int


class HandoffOut(BaseModel):
    id: uuid.UUID
    ngo: NgoCard
    hub_id: uuid.UUID
    kind: Gift
    donation_ids: list[uuid.UUID] = Field(description="items: the verified donations handed over")
    offer_ids: list[uuid.UUID] = Field(description="food: the offers this NGO claimed")
    meals: int = Field(description="meals: the donated meals this hand-off serves")
    summary: HandoffSummary
    photos: int
    photo_urls: list[PhotoUrl] = Field(description="5-minute links; minting them is audit-logged")
    people_reached: int | None = Field(description="As the NGO reports it")
    handed_at: datetime
    recorded_by: Literal["hub", "ngo"]
    confirmed_at: datetime | None = Field(description="When the NGO's coordinator confirmed")
    note: str | None


class HandoffReport(BaseModel):
    """Totals over every hand-off the filter matches, not only this page."""

    handoffs: int
    items: int
    kg: float
    meals: int
    people_reached: int


class HandoffsOut(BaseModel):
    items: list[HandoffOut]
    next_cursor: str | None
    report: HandoffReport


class ReadyLine(BaseModel):
    code: str = Field(examples=["clothes"])
    unit: Literal["kg", "each"]
    qty: float = Field(description="What the hub counted")


class ReadyDonation(BaseModel):
    """A verified donation not yet handed over: items and the date, never a household or flat."""

    id: uuid.UUID
    items: list[ReadyLine]
    verified_on: date = Field(description="IST")


class ReadyOut(BaseModel):
    donations: list[ReadyDonation]
    offers: list[NgoOfferOut] = Field(description="Claimed, not yet handed over")
    meals_waiting: int = Field(description="Donated meals of the hub not yet served")


class HandoffIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ngo_id: uuid.UUID
    hub_id: uuid.UUID
    kind: Gift
    donation_ids: list[uuid.UUID] = Field(default_factory=list[uuid.UUID], max_length=100)
    offer_ids: list[uuid.UUID] = Field(default_factory=list[uuid.UUID], max_length=100)
    meals: int = Field(default=0, ge=0, le=10_000)
    photo_keys: list[str] = Field(
        default_factory=list[str],
        max_length=3,
        description="1 to 3 of your own `handoff_photo` uploads: goods and the hand-over, "
        "never beneficiaries' faces",
    )
    people_reached: int | None = Field(default=None, ge=0, le=100_000)
    handed_at: datetime | None = Field(default=None, description="Default now; never ahead")
    note: str | None = Field(default=None, max_length=300)

    _donations = field_validator("donation_ids")(_distinct)
    _offers = field_validator("offer_ids")(_distinct)


class ConfirmIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    people_reached: int | None = Field(default=None, ge=0, le=100_000)
    photo_keys: list[str] = Field(
        default_factory=list[str], max_length=3, description="More `handoff_photo` uploads"
    )
