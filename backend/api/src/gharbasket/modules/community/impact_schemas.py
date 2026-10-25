"""Response models of `GET /v1/impact` (M9 §3.7). No household, name or flat anywhere: a society's
numbers are `null` until 5 households contributed (k = 5)."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class DonatedLineOut(BaseModel):
    code: str = Field(examples=["clothes"])
    unit: Literal["kg", "each"]
    qty: float = Field(description="Accepted (counted) total")


class MyImpactOut(BaseModel):
    scope: Literal["me"] = "me"
    period: Literal["all"] = "all"
    items_donated: int = Field(
        description="Accepted pieces, plus kilograms counted as one item per kg"
    )
    kg_donated: float
    donations_verified: int
    points_earned: int = Field(
        description="All Kindness points ever credited (donations, referrals)"
    )
    meals_donated: int = Field(description="Meals given with points")
    homes_invited: int = Field(description="Converted referrals")
    donated: list[DonatedLineOut] = Field(description="Accepted totals per code")


class HubImpactOut(BaseModel):
    food_saved_kg: float = Field(description="Surplus the society's hub handed to NGOs")
    meals_served: int = Field(description="Donated meals NGOs served from the hub")
    partners_earned_paise: int | None = Field(
        description="The hub's partners this month (the asked one; this month for `all`); "
        "null under 5 paid partners"
    )
    partners: Literal["5+", "under_5"]


class SocietyImpactOut(BaseModel):
    scope: Literal["society"] = "society"
    society_id: uuid.UUID
    period: str = Field(description="`all` or an IST month, YYYY-MM", examples=["all", "2027-02"])
    as_of: datetime = Field(description="The time of the read: the numbers are computed live")
    homes_joined: int | None = Field(description="Null under 5 households")
    items_donated: int | None = Field(description="Null until 5 households contributed")
    kg_donated: float | None
    meals_donated: int | None
    contributors: Literal["5+", "under_5"]
    hub: HubImpactOut | None = Field(description="Null for a society without a hub")
