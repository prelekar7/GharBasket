"""Response models of the impact endpoints (M12 §3.6, §3.7): the console's impact report and the
public society page. Aggregates only: no household, name, flat, phone or per-partner number."""

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

ImpactScope = Literal["platform", "hub", "ngo"]


class ImpactTotalsOut(BaseModel):
    """The month's numbers. A field that does not apply to the scope, or that the facts cannot
    give, is null: an NGO coordinator's report carries only what the NGO received."""

    food_saved_kg: float = Field(description="Surplus food handed to NGOs (kind food)")
    items_donated: int = Field(
        description="Items of verified donations; for an NGO, items it received"
    )
    kg_donated: float = Field(description="Kilograms of those items")
    donations_verified: int | None
    households_donating: int | None = Field(description="Null under 5")
    meals_donated: int | None = Field(description="Meals pledged with Kindness points")
    meals_served: int = Field(description="Donated meals NGOs served")
    people_reached: int | None = Field(description="As NGOs report it; null if none reported")
    referrals_converted: int | None
    societies_live: int | None
    partners_earned_paise: int | None = Field(
        description="Null under 5 paid partners; never for scope ngo"
    )
    partners_paid: int | None = Field(description="Null under 5 paid partners; never for ngo")
    waste_g_per_100kg: int | None = Field(description="Hub and platform only; bin grams per 100 kg")


class ImpactTrendOut(BaseModel):
    months: list[str] = Field(description="The 6 months up to `month`, oldest first (YYYY-MM)")
    food_saved_kg: list[float]
    items_donated: list[int]
    meals_served: list[int]


class ImpactSocietyOut(BaseModel):
    society_id: uuid.UUID
    name: str
    items_donated: int
    kg_donated: float
    meals_donated: int | None = Field(
        description="Null: a donated meal belongs to a hub, not a society, in the facts"
    )
    households_joined: int


class ImpactNgoOut(BaseModel):
    ngo_id: uuid.UUID
    name: str
    handoffs: int
    items: int
    kg: float
    meals: int
    people_reached: int | None


class ImpactOut(BaseModel):
    scope: ImpactScope
    hub_id: uuid.UUID | None
    ngo_id: uuid.UUID | None
    month: str = Field(examples=["2027-02"])
    data_source: Literal["real", "synthetic"]
    as_of: datetime | None
    stale: bool
    totals: ImpactTotalsOut
    trend: ImpactTrendOut
    societies: list[ImpactSocietyOut] | None = Field(
        description="Scope hub only; no earnings. Null for platform and ngo"
    )
    ngos: list[ImpactNgoOut]


class PublicImpactOut(BaseModel):
    items_donated: int
    meals_donated: int


class PublicSocietyOut(BaseModel):
    slug: str = Field(examples=["sunrise-towers-kharghar"])
    name: str
    area: str | None
    status: Literal["unlocking", "live"]
    homes_joined: int | None = Field(description="Null under 5 households")
    unlock_target: int
    progress_bp: int = Field(description="min(joined / target, 1) in basis points")
    live_since: date | None = Field(description="IST date")
    delivery_days: list[Literal["mon", "tue", "wed", "thu", "fri", "sat", "sun"]] = Field(
        description="Live societies only"
    )
    impact: PublicImpactOut | None = Field(description="Null until 5 households contributed")
    join_url: str
