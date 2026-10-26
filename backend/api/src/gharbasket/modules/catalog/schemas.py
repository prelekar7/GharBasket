"""Request and response models of M2 Catalog & Pricing.

The product, search, price and substitute shapes are the frozen S1 contract
(docs/modules/M2-catalog.md §3.1): changing them needs a PR reviewed by both app owners.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, StringConstraints, field_validator

from gharbasket.modules.catalog.models import SLUG

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


class ProductNutrition(BaseModel):
    """Per 100 g, from IFCT 2017 (ICMR-NIN); `unit_g` is the weight of one piece or bunch."""

    kcal: int
    protein_g: float
    fat_g: float
    carb_g: float
    fibre_g: float
    unit_g: int | None = None


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
    nutrition: ProductNutrition | None = Field(default=None, description="Per 100 g (IFCT 2017)")


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


def _one_price_per_product(items: list[PriceItem]) -> list[PriceItem]:
    if len({item.product_id for item in items}) != len(items):
        raise ValueError("each product once")
    return items


class PriceListIn(BaseModel):
    model_config = {"extra": "forbid"}

    valid_from: date = Field(description="Today or later, in Asia/Kolkata")
    items: list[PriceItem] = Field(min_length=1, max_length=1000)

    _unique = field_validator("items")(_one_price_per_product)


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
    is_side: bool = Field(description="Eaten with a main (chapati, rice); never planned alone")
    sides: list[str] = Field(description="Slugs of side recipes this one is eaten with")


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


# --- catalog admin (spec §3.3) ------------------------------------------------------------------

TermLang = Literal["en", "hi", "mr", "hinglish"]


class CategoryOut(BaseModel):
    id: uuid.UUID
    slug: str = Field(examples=["leafy-greens"])
    name: str
    names: Names
    parent_id: uuid.UUID | None


class Categories(BaseModel):
    items: list[CategoryOut]


class NamesIn(BaseModel):
    """All three are required; a missing or blank one is 422 `catalog.names_incomplete`."""

    model_config = {"extra": "forbid"}

    en: str | None = Field(default=None, max_length=80)
    hi: str | None = Field(default=None, max_length=80)
    mr: str | None = Field(default=None, max_length=80)


class StepIn(BaseModel):
    model_config = {"extra": "forbid"}

    qty: int = Field(ge=1, le=100_000, examples=[250])
    unit: Unit


class Nutrition(BaseModel):
    """Per 100 g; `unit_g` is the weight of one piece or bunch."""

    model_config = {"extra": "forbid"}

    kcal: int = Field(ge=0)
    protein_g: float = Field(ge=0)
    fat_g: float = Field(ge=0)
    carb_g: float = Field(ge=0)
    fibre_g: float = Field(ge=0)
    unit_g: int | None = Field(default=None, ge=0)
    source: Literal["ifct2017", "nin_nvif", "label", "manual"]


class SynonymIn(BaseModel):
    model_config = {"extra": "forbid"}

    term: str = Field(min_length=1, max_length=60, examples=["lady finger"])
    lang: TermLang


TagCode = Annotated[str, Field(max_length=40)]


class ProductIn(BaseModel):
    model_config = {"extra": "forbid"}

    slug: str = Field(min_length=2, max_length=60, pattern=SLUG, examples=["bhindi"])
    kind: str = Field(
        max_length=20, description="veg, fruit, staple or salad (else catalog.kind_not_editable)"
    )
    category_id: uuid.UUID
    names: NamesIn
    step: StepIn
    shelf_life_h: int | None = Field(default=None, ge=1, le=8760)
    tags: list[TagCode] = Field(default=[], max_length=20, description="Codes from catalog.TAGS")
    nutrition: Nutrition | None = None
    synonyms: list[SynonymIn] = Field(default=[], max_length=30)


class ProductPatch(BaseModel):
    """Only the fields sent change. slug, kind and step never change (catalog.field_immutable)."""

    model_config = {"extra": "forbid"}

    slug: str | None = Field(default=None, description="Refused: catalog.field_immutable")
    kind: str | None = Field(default=None, description="Refused: catalog.field_immutable")
    step: StepIn | None = Field(default=None, description="Refused: catalog.field_immutable")
    category_id: uuid.UUID | None = None
    names: NamesIn | None = None
    shelf_life_h: int | None = Field(default=None, ge=1, le=8760)
    tags: list[TagCode] | None = Field(default=None, max_length=20)
    nutrition: Nutrition | None = None
    synonyms: list[SynonymIn] | None = Field(default=None, max_length=30)
    active: bool | None = None


class UserRef(BaseModel):
    id: uuid.UUID
    name: str | None


class AdminProduct(BaseModel):
    id: uuid.UUID
    slug: str
    kind: Kind
    category: CategoryRef
    name: str
    names: Names
    step: Step
    tags: list[str]
    shelf_life_h: int | None
    image_url: str | None
    active: bool
    version: int
    nutrition: Nutrition | None
    synonyms: list[SynonymIn]
    used_by_recipes: int = Field(description="Active recipes with this product as an ingredient")
    data_source: Literal["real", "synthetic"]
    updated_at: datetime
    updated_by: UserRef | None = Field(description="Who last edited it in the console")


class ProductSaved(AdminProduct):
    recipes_recomputed: list[str] = Field(
        default=[], description="Slugs of recipes whose derived rules changed with the tags"
    )


class FieldChange(BaseModel):
    from_: Any = Field(alias="from")
    to: Any


class HistoryEntry(BaseModel):
    at: datetime
    by: UserRef | None
    action: str = Field(examples=["catalog.product_updated"])
    changed: dict[str, FieldChange]


class History(BaseModel):
    items: list[HistoryEntry] = Field(description="Newest first, at most 100")


# --- the console's recipe editor (spec §3.3) -----------------------------------------------------

RecipeTag = Annotated[str, Field(pattern=r"^[a-z0-9_]{1,40}$", examples=["one_pot"])]
RecipeSlug = Annotated[str, Field(min_length=2, max_length=60, pattern=SLUG)]


class StepTextIn(BaseModel):
    model_config = {"extra": "forbid"}

    en: str = Field(min_length=1, max_length=500)
    hi: str | None = Field(default=None, max_length=500)
    mr: str | None = Field(default=None, max_length=500)


class RecipeStep(BaseModel):
    model_config = {"extra": "forbid"}

    n: int = Field(ge=1, le=30, description="1, 2, 3 … in order")
    text: StepTextIn


class IngredientIn(BaseModel):
    model_config = {"extra": "forbid"}

    product_id: uuid.UUID
    qty: Decimal = Field(gt=0, max_digits=8, decimal_places=2, examples=[50])
    unit: IngredientUnit = Field(description="Fits the product's step (catalog.unit_mismatch)")
    optional: bool = False


def _distinct_products(items: list[IngredientIn] | None) -> list[IngredientIn] | None:
    if items is not None and len({i.product_id for i in items}) != len(items):
        raise ValueError("each product once")
    return items


def _numbered(steps: list[RecipeStep] | None) -> list[RecipeStep] | None:
    if steps is not None and [s.n for s in steps] != list(range(1, len(steps) + 1)):
        raise ValueError("steps are numbered 1, 2, 3 … in order")
    return steps


class RecipeIn(BaseModel):
    """The derived rules (diet, jain_ok, no_onion_garlic) are calculated, never sent."""

    model_config = {"extra": "forbid"}

    slug: RecipeSlug
    names: NamesIn
    cuisine: Cuisine
    slots: list[Slot] = Field(min_length=1, max_length=3)
    cook_minutes: int = Field(ge=5, le=240)
    servings: int = Field(ge=1, le=12)
    tags: list[RecipeTag] = Field(default=[], max_length=20)
    is_side: bool = False
    sides: list[RecipeSlug] = Field(default=[], max_length=10)
    fasting_ok: bool
    diabetic_ok: bool
    ingredients: list[IngredientIn] = Field(min_length=1, max_length=30)
    steps: list[RecipeStep] | None = Field(default=None, max_length=30)

    _ingredients = field_validator("ingredients")(_distinct_products)
    _steps = field_validator("steps")(_numbered)


class RecipePatch(BaseModel):
    """Only the fields sent change; ingredients and steps are replaced whole. A catalog editor
    may send only names, fasting_ok and diabetic_ok."""

    model_config = {"extra": "forbid"}

    slug: str | None = Field(default=None, description="Refused: catalog.field_immutable")
    names: NamesIn | None = None
    cuisine: Cuisine | None = None
    slots: list[Slot] | None = Field(default=None, min_length=1, max_length=3)
    cook_minutes: int | None = Field(default=None, ge=5, le=240)
    servings: int | None = Field(default=None, ge=1, le=12)
    tags: list[RecipeTag] | None = Field(default=None, max_length=20)
    is_side: bool | None = None
    sides: list[RecipeSlug] | None = Field(default=None, max_length=10)
    fasting_ok: bool | None = None
    diabetic_ok: bool | None = None
    ingredients: list[IngredientIn] | None = Field(default=None, min_length=1, max_length=30)
    steps: list[RecipeStep] | None = Field(default=None, max_length=30)
    active: bool | None = None

    _ingredients = field_validator("ingredients")(_distinct_products)
    _steps = field_validator("steps")(_numbered)


class Review(BaseModel):
    by: UserRef | None
    at: datetime


class Reviews(BaseModel):
    fasting_ok: Review | None = Field(description="Who last set the flag, and when")
    diabetic_ok: Review | None


class AdminRecipe(RecipeSummary):
    ingredients: list[IngredientOut]
    active: bool
    version: int
    steps: list[RecipeStep] | None = Field(description="Left out (null) in the list")
    reviews: Reviews
    updated_at: datetime
    updated_by: UserRef | None = Field(description="Who last edited it in the console")


# --- the console's festival calendar and price lists (spec §3.3) ---------------------------------


class FestivalRulesIn(BaseModel):
    model_config = {"extra": "forbid"}

    fasting: bool
    no_onion_garlic: bool


class FestivalIn(BaseModel):
    model_config = {"extra": "forbid"}

    date: date  # within two years of today
    code: str = Field(pattern=r"^[a-z0-9_]{2,40}$", examples=["navratri"])
    region: str = Field(max_length=40, examples=["all", "maharashtra"])
    names: NamesIn
    rules: FestivalRulesIn
    verified: bool = False


class FestivalPatch(BaseModel):
    """Only the fields sent change; last write wins (no If-Match)."""

    model_config = {"extra": "forbid"}

    names: NamesIn | None = None
    rules: FestivalRulesIn | None = None
    verified: bool | None = None


class AdminFestival(BaseModel):
    date: date
    code: str
    region: str
    names: Names
    rules: FestivalRules
    verified: bool
    source: Literal["panchang", "manual"] = Field(description="manual: added in the console")
    updated_at: datetime
    updated_by: UserRef | None


class AdminFestivals(BaseModel):
    items: list[AdminFestival]


class PriceListItemsIn(BaseModel):
    model_config = {"extra": "forbid"}

    items: list[PriceItem] = Field(min_length=1, max_length=1000)

    _unique = field_validator("items")(_one_price_per_product)


class PriceListSummary(BaseModel):
    id: uuid.UUID
    hub_id: uuid.UUID
    valid_from: date
    items_count: int
    version: int
    published_by: UserRef | None
    created_at: datetime
    updated_at: datetime
    editable: bool = Field(description="valid_from is today or later (India time)")


class PriceListLine(BaseModel):
    product_id: uuid.UUID
    slug: str
    name: str
    step: Step
    price_paise: int
    mrp_paise: int | None


class PriceLists(BaseModel):
    items: list[PriceListSummary] = Field(description="Newest first")


class PriceListDetail(PriceListSummary):
    items: list[PriceListLine]


# --- the tiffin menu review (§3.4) ---------------------------------------------------------------

MenuStatus = Literal["submitted", "approved", "sent_back", "withdrawn"]


class MenuKitchen(BaseModel):
    partner_id: uuid.UUID
    display_name: str | None = Field(description="The kitchen's name; null if the partner is gone")


class MenuProduct(BaseModel):
    id: uuid.UUID
    slug: str
    name: str
    tags: list[str]


class MenuDish(BaseModel):
    recipe_id: uuid.UUID
    name: str


class MenuRules(BaseModel):
    """True only when every dish's recipe has the rule (§3.2); false for a menu without dishes."""

    jain_ok: bool
    no_onion_garlic: bool
    fasting_ok: bool
    diabetic_ok: bool


class TiffinMenuReview(BaseModel):
    id: uuid.UUID
    hub_id: uuid.UUID
    date: date
    meal: Literal["lunch", "dinner"]
    status: MenuStatus
    kitchen: MenuKitchen
    product: MenuProduct
    dishes: list[MenuDish]
    price_paise: int = Field(description="Per portion, the menu's own price")
    rules: MenuRules
    contains: list[str] = Field(description="The dishes' non-optional ingredients, in the language")
    order_by: datetime = Field(description="The date's cut-off: 21:00 IST the day before")
    submitted_at: datetime
    decided_at: datetime | None
    decided_by: UserRef | None
    note: str | None = Field(description="The send-back reason")
    version: int


class SendBackIn(BaseModel):
    model_config = {"extra": "forbid"}

    note: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
