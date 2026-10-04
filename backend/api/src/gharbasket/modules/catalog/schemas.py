"""Request and response models of M2 Catalog & Pricing.

The product, search, price and substitute shapes are the frozen S1 contract
(docs/modules/M2-catalog.md §3.1): changing them needs a PR reviewed by both app owners.
"""

import uuid
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

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
