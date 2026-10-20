"""Tables of M2 Catalog & Pricing. Every table sets `__table_args__ = {"schema": SCHEMA}`.

Categories, products, the search terms that make one product findable by every spelling, hub
price lists (spec §3.1), recipes with their ingredients and the festival calendar (§3.2), and
kitchens' tiffin menus (§4; booked through M4 §3.5).
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from gharbasket.platform.db import Base

SCHEMA = "catalog"

KINDS = ("veg", "fruit", "staple", "kit", "tiffin", "salad")
UNITS = ("g", "kg", "ml", "l", "piece", "bunch", "pack")
TERM_LANGS = ("en", "hi", "mr", "hinglish")
TERM_SOURCES = ("name", "synonym", "transliteration")
SLUG = r"^[a-z0-9]+(-[a-z0-9]+)*$"
CUISINES = ("maharashtrian", "gujarati", "north_indian", "south_indian", "bengali", "common")
SLOTS = ("breakfast", "lunch", "dinner")
DIETS = ("veg", "egg")
INGREDIENT_UNITS = ("g", "ml", "piece", "bunch")
MEALS = ("lunch", "dinner")
MENU_STATUSES = ("draft", "approved", "withdrawn")
# Product tag codes (spec §3.3). Recipes' derived rules read `root`, `onion_garlic` and `egg`.
TAGS = (
    "dairy",
    "egg",
    "fasting_ok",
    "gourd",
    "high_fibre",
    "high_protein",
    "leafy",
    "onion_garlic",
    "root",
)
FESTIVAL_SOURCES = ("panchang", "manual")
# State and union territory codes a festival day may be limited to (or "all"), spec §3.3.
REGIONS = (
    "andhra_pradesh", "arunachal_pradesh", "assam", "bihar", "chhattisgarh", "goa", "gujarat",
    "haryana", "himachal_pradesh", "jharkhand", "karnataka", "kerala", "madhya_pradesh",
    "maharashtra", "manipur", "meghalaya", "mizoram", "nagaland", "odisha", "punjab",
    "rajasthan", "sikkim", "tamil_nadu", "telangana", "tripura", "uttar_pradesh", "uttarakhand",
    "west_bengal", "andaman_nicobar", "chandigarh", "dadra_nagar_haveli_daman_diu", "delhi",
    "jammu_kashmir", "ladakh", "lakshadweep", "puducherry",
)  # fmt: skip
# Nutrition sources by the names the seed file uses; products store the codes (§3.3).
NUTRITION_SOURCES = {"IFCT 2017": "ifct2017", "NIN Nutritive Value of Indian Foods": "nin_nvif"}

# Search compares lower-cased, accent-free text. The migration creates `catalog.f_unaccent`, an
# IMMUTABLE wrapper (unaccent() itself is only STABLE, so it cannot feed a generated column).
TERM_NORM = "catalog.f_unaccent(lower(term))"


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _has_all_languages(column: str) -> str:
    return f"{column} ?& array['en', 'hi', 'mr']"


class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (
        CheckConstraint(f"slug ~ '{SLUG}'", name="slug"),
        CheckConstraint(_has_all_languages("names"), name="names"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    slug: Mapped[str] = mapped_column(String(60), unique=True)
    # {"en": ..., "hi": ..., "mr": ...}
    names: Mapped[dict[str, str]] = mapped_column(JSONB)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.categories.id", ondelete="RESTRICT")
    )
    sort_order: Mapped[int] = mapped_column(Integer, server_default=text("0"))


class Product(Base):
    """Anything that can be planned or sold. Prices live in price lists, per hub."""

    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint(f"slug ~ '{SLUG}'", name="slug"),
        CheckConstraint(_one_of("kind", KINDS), name="kind"),
        CheckConstraint(_one_of("step_unit", UNITS), name="step_unit"),
        CheckConstraint("step_qty > 0", name="step_qty"),
        # Every product has a name in all three languages before it can be sold (spec §9).
        CheckConstraint(_has_all_languages("names"), name="names"),
        CheckConstraint(_one_of("data_source", ("real", "synthetic")), name="data_source"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    slug: Mapped[str] = mapped_column(String(60), unique=True)
    kind: Mapped[str] = mapped_column(String(8))
    category_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.categories.id", ondelete="RESTRICT"), index=True
    )
    names: Mapped[dict[str, str]] = mapped_column(JSONB)
    # The unit a person adds or removes in the app (one 250 g pack); prices are per step.
    step_qty: Mapped[int] = mapped_column(Integer)
    step_unit: Mapped[str] = mapped_column(String(8))
    shelf_life_h: Mapped[int | None] = mapped_column(Integer)
    # Per 100 g from IFCT 2017: {kcal, protein_g, fat_g, carb_g, fibre_g, unit_g?, source}
    # (data/seeds/catalog/nutrition.json); null for products without a row yet.
    nutrition: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Codes such as "leafy", "root", "onion_garlic", "fasting_ok", "high_protein".
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    image_key: Mapped[str | None] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    data_source: Mapped[str] = mapped_column(String(16), server_default="real")
    # Optimistic locking for the console (§3.3); `updated_by` set means a console edit, which
    # `just seed` never overwrites.
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    updated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SearchTerm(Base):
    """One spelling a product can be found by: a name, a synonym or a transliteration."""

    __tablename__ = "search_terms"
    __table_args__ = (
        UniqueConstraint("product_id", "term"),
        CheckConstraint(_one_of("lang", TERM_LANGS), name="lang"),
        CheckConstraint(_one_of("source", TERM_SOURCES), name="source"),
        Index(
            "ix_search_terms_term_norm",
            "term_norm",
            postgresql_using="gin",
            postgresql_ops={"term_norm": "gin_trgm_ops"},
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.products.id", ondelete="CASCADE"), index=True
    )
    term: Mapped[str] = mapped_column(String(120))
    term_norm: Mapped[str] = mapped_column(Text, Computed(TERM_NORM, persisted=True))
    lang: Mapped[str] = mapped_column(String(10))
    source: Mapped[str] = mapped_column(String(16))


class PriceList(Base):
    """A hub's prices from `valid_from` on; the newest list on or before a date is current."""

    __tablename__ = "price_lists"
    __table_args__ = (
        UniqueConstraint("hub_id", "valid_from"),
        CheckConstraint(_one_of("data_source", ("real", "synthetic")), name="data_source"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    # No foreign key: hub ids are checked through hub_ops.api (ADR-0026).
    hub_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    valid_from: Mapped[date] = mapped_column(Date)
    published_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    data_source: Mapped[str] = mapped_column(String(16), server_default="real")
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    updated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Price(Base):
    """The price of one step of a product (e.g. one 250 g pack), in paise."""

    __tablename__ = "prices"
    __table_args__ = (
        CheckConstraint("price_paise > 0", name="price_paise"),
        CheckConstraint("mrp_paise IS NULL OR mrp_paise >= price_paise", name="mrp_paise"),
        {"schema": SCHEMA},
    )

    price_list_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.price_lists.id", ondelete="CASCADE"), primary_key=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.products.id", ondelete="RESTRICT"), primary_key=True, index=True
    )
    price_paise: Mapped[int] = mapped_column(BigInteger)
    # Printed MRP for packaged goods; null for fresh produce.
    mrp_paise: Mapped[int | None] = mapped_column(BigInteger)


class Recipe(Base):
    """A dish the planner can choose; jain_ok and no_onion_garlic come from its ingredients."""

    __tablename__ = "recipes"
    __table_args__ = (
        CheckConstraint(f"slug ~ '{SLUG}'", name="slug"),
        CheckConstraint(_has_all_languages("names"), name="names"),
        CheckConstraint(_one_of("cuisine", CUISINES), name="cuisine"),
        CheckConstraint(_one_of("diet", DIETS), name="diet"),
        CheckConstraint(
            f"cardinality(slots) > 0 AND slots <@ ARRAY{list(SLOTS)}::text[]", name="slots"
        ),
        CheckConstraint("cook_minutes BETWEEN 1 AND 600", name="cook_minutes"),
        CheckConstraint("servings BETWEEN 1 AND 50", name="servings"),
        CheckConstraint(_one_of("data_source", ("real", "synthetic")), name="data_source"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    names: Mapped[dict[str, str]] = mapped_column(JSONB)
    cuisine: Mapped[str] = mapped_column(String(20))
    slots: Mapped[list[str]] = mapped_column(ARRAY(Text))
    cook_minutes: Mapped[int] = mapped_column(Integer)
    servings: Mapped[int] = mapped_column(Integer)
    diet: Mapped[str] = mapped_column(String(8))
    # {"jain_ok", "no_onion_garlic"} (derived) and {"fasting_ok", "diabetic_ok"} (curated).
    rules: Mapped[dict[str, bool]] = mapped_column(JSONB)
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    # Side recipes (chapati, rice) are eaten with a main and never planned alone (M2 §3.2).
    is_side: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    sides: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))  # slugs
    steps: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    data_source: Mapped[str] = mapped_column(String(16), server_default="real")
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    updated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    # Who last changed each curated flag, and when (§3.3 `reviews`).
    fasting_reviewed_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    fasting_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    diabetic_reviewed_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    diabetic_reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class RecipeIngredient(Base):
    __tablename__ = "recipe_ingredients"
    __table_args__ = (
        CheckConstraint("qty_per_serving > 0", name="qty_per_serving"),
        CheckConstraint(_one_of("unit", INGREDIENT_UNITS), name="unit"),
        {"schema": SCHEMA},
    )

    recipe_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.recipes.id", ondelete="CASCADE"), primary_key=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.products.id", ondelete="RESTRICT"), primary_key=True, index=True
    )
    # Grams, millilitres, pieces or bunches for one serving.
    qty_per_serving: Mapped[Decimal] = mapped_column(Numeric(8, 2))
    unit: Mapped[str] = mapped_column(String(8))
    optional: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))


class Festival(Base):
    """A fasting or festival day. `verified` once checked against a panchang."""

    __tablename__ = "festival_calendar"
    __table_args__ = (
        CheckConstraint(_has_all_languages("names"), name="names"),
        CheckConstraint(_one_of("source", FESTIVAL_SOURCES), name="source"),
        {"schema": SCHEMA},
    )

    # The column is "date"; the attribute is `day` so it does not shadow the `date` type.
    day: Mapped[date] = mapped_column("date", Date, primary_key=True)
    code: Mapped[str] = mapped_column(String(40), primary_key=True)
    # "all" or a state code such as "maharashtra".
    region: Mapped[str] = mapped_column(String(40), primary_key=True)
    names: Mapped[dict[str, str]] = mapped_column(JSONB)
    # {"fasting": bool, "no_onion_garlic": bool}
    rules: Mapped[dict[str, bool]] = mapped_column(JSONB)
    verified: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    # "manual" rows (added in the console) and verified ones are never overwritten by a seed.
    source: Mapped[str] = mapped_column(String(16), server_default="panchang")
    updated_by: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TiffinMenu(Base):
    """One kitchen partner's tiffin offer for a date and meal (spec §4), shown to the customers of
    her hub once a hub manager approves it. Its `partner_id` is the customer's preferred cook, not
    a hard choice (M8 §3.4); `product_id` is the tiffin product whose tags carry the skills."""

    __tablename__ = "tiffin_menus"
    __table_args__ = (
        UniqueConstraint("partner_id", "date", "meal"),
        CheckConstraint(_one_of("meal", MEALS), name="meal"),
        CheckConstraint(_one_of("status", MENU_STATUSES), name="status"),
        CheckConstraint("price_paise > 0", name="price_paise"),
        Index("ix_tiffin_menus_hub_id_date_status", "hub_id", "date", "status"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text("uuidv7()"))
    # M6's hub and M8's kitchen partner: plain UUIDs, no foreign keys across schemas.
    hub_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    partner_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    on_date: Mapped[date] = mapped_column("date", Date)
    meal: Mapped[str] = mapped_column(String(8))
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.products.id", ondelete="RESTRICT")
    )
    recipe_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid), server_default=text("'{}'"))
    price_paise: Mapped[int] = mapped_column(BigInteger)  # per portion
    status: Mapped[str] = mapped_column(String(12), server_default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
