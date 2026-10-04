"""Tables of M2 Catalog & Pricing. Every table sets `__table_args__ = {"schema": SCHEMA}`.

Products part (this file, so far): categories, products and the search terms that make one product
findable by every spelling (spec §3.1). Prices, substitutes, recipes and festivals follow.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
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
    # Per 100 g; null until the nutrition source (IFCT 2017) is settled (spec §11).
    nutrition: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Codes such as "leafy", "root", "onion_garlic", "fasting_ok", "high_protein".
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default=text("'{}'"))
    image_key: Mapped[str | None] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    data_source: Mapped[str] = mapped_column(String(16), server_default="real")
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
