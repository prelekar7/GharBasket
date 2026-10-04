"""Request and response models of M2 Catalog & Pricing.

The product, search, price and substitute shapes are the frozen S1 contract
(docs/modules/M2-catalog.md §3.1): changing them needs a PR reviewed by both app owners.
"""

import uuid
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Kind = Literal["veg", "fruit", "staple", "kit", "tiffin", "salad"]
Unit = Literal["g", "kg", "ml", "l", "piece", "bunch", "pack"]


class Names(BaseModel):
    en: str
    hi: str
    mr: str


class Step(BaseModel):
    """The unit a person adds or removes in the app; prices are per step."""

    qty: int = Field(examples=[250])
    unit: Unit
    label: str = Field(examples=["250 g"], description="In the requested language")


class CategoryRef(BaseModel):
    id: uuid.UUID
    slug: str = Field(examples=["leafy-greens"])
    name: str = Field(examples=["Leafy greens"])


class Price(BaseModel):
    price_paise: int = Field(examples=[2000], description="For one step, in paise")
    mrp_paise: int | None
    hub_id: uuid.UUID
    valid_from: date


class ProductOut(BaseModel):
    id: uuid.UUID
    slug: str = Field(examples=["bhindi"])
    kind: Kind
    category: CategoryRef
    name: str = Field(examples=["Okra (bhindi)"], description="In the requested language")
    names: Names
    step: Step
    tags: list[str] = Field(examples=[["green", "high_fibre"]])
    shelf_life_h: int | None
    image_url: str | None
    price: Price | None = Field(description="For the caller's hub; null without one")


class SearchItem(BaseModel):
    product_id: uuid.UUID
    slug: str
    name: str
    matched: str = Field(examples=["bhendi"], description="The spelling that matched")
    step: Step
    price_paise: int | None


class SearchResults(BaseModel):
    items: list[SearchItem]


class SubstituteOut(BaseModel):
    product_id: uuid.UUID
    slug: str
    name: str
    step: Step
    price_paise: int | None
    price_delta_paise: int | None = Field(
        description="Substitute price minus the original's, per step; null without both prices"
    )
    score: float = Field(ge=0, le=1, examples=[0.82])
    reason_code: Literal["similar_use", "same_family", "cheaper", "in_season"]
    reason_params: dict[str, int | str] = Field(examples=[{"saves_paise": 200}])


class Substitutes(BaseModel):
    items: list[SubstituteOut]


class PriceItem(BaseModel):
    model_config = {"extra": "forbid"}

    product_id: uuid.UUID
    price_paise: int = Field(gt=0, le=10_000_000, examples=[2000])
    mrp_paise: int | None = Field(default=None, gt=0, le=10_000_000, examples=[2400])


class PriceListOut(BaseModel):
    id: uuid.UUID
    hub_id: uuid.UUID
    valid_from: date
    items: list[PriceItem]


class PriceListIn(BaseModel):
    model_config = {"extra": "forbid"}

    valid_from: date = Field(description="Today or later, in Asia/Kolkata")
    items: list[PriceItem] = Field(min_length=1, max_length=1000)

    @field_validator("items")
    @classmethod
    def _one_price_per_product(cls, items: list[PriceItem]) -> list[PriceItem]:
        if len({item.product_id for item in items}) != len(items):
            raise ValueError("each product once")
        return items


class PriceListCreated(BaseModel):
    id: uuid.UUID
    hub_id: uuid.UUID
    valid_from: date
    items_count: int


# --- recipes and festivals (spec §3.2) ---------------------------------------------------------

Cuisine = Literal["maharashtrian", "gujarati", "north_indian", "south_indian", "bengali", "common"]
Slot = Literal["breakfast", "lunch", "dinner"]
IngredientUnit = Literal["g", "ml", "piece", "bunch"]


class RecipeRules(BaseModel):
    jain_ok: bool = Field(description="Derived: no root, onion or garlic ingredient")
    no_onion_garlic: bool = Field(description="Derived: no onion or garlic ingredient")
    fasting_ok: bool = Field(description="Curated: commonly eaten on vrat days")
    diabetic_ok: bool = Field(description="Curated")


class RecipeSummary(BaseModel):
    id: uuid.UUID
    slug: str = Field(examples=["sabudana-khichdi"])
    name: str
    names: Names
    cuisine: Cuisine
    slots: list[Slot]
    cook_minutes: int
    servings: int
    diet: Literal["veg", "egg"]
    rules: RecipeRules
    tags: list[str]


class IngredientOut(BaseModel):
    product_id: uuid.UUID
    slug: str
    name: str
    qty: float = Field(examples=[50], description="Per serving")
    unit: IngredientUnit
    label: str = Field(examples=["50 g"])
    optional: bool


class RecipeOut(RecipeSummary):
    ingredients: list[IngredientOut]
    cost_per_serving_paise: int | None = Field(
        description="From the caller's hub prices; null if an ingredient has no price there"
    )


class FestivalRules(BaseModel):
    fasting: bool
    no_onion_garlic: bool


class FestivalOut(BaseModel):
    date: date
    code: str = Field(examples=["navratri"])
    name: str
    region: str = Field(examples=["all"])
    rules: FestivalRules
    verified: bool = Field(description="Checked against a panchang; unverified dates are flagged")


class Festivals(BaseModel):
    items: list[FestivalOut]
