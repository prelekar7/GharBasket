"""Business logic of M2 Catalog & Pricing: products, step labels and multilingual search.

Search (spec §3.1, US-1) looks a product up by any of its spellings in `catalog.search_terms`:
names in English, Hindi and Marathi, curated synonyms ("lady finger"), and the Hinglish form of
every Devanagari name ("भिंडी" → "bhindi"). A Devanagari query is also tried in its Hinglish form,
so search works even where the database's trigram index cannot read Devanagari.
"""

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Float, func, or_, select, text, tuple_
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog.models import Category, Product, SearchTerm
from gharbasket.modules.catalog.spelling import has_devanagari, hinglish
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.locale import Language
from gharbasket.platform.pagination import decode_cursor, encode_cursor

# A typed word must cover this much of a spelling to count as a match (pg_trgm word similarity):
# low enough for typos ("bindi" → "bhindi"), high enough to keep unrelated items out.
MATCH_THRESHOLD = 0.4

UNIT_LABELS: dict[str, dict[Language, str]] = {
    "g": {"en": "g", "hi": "ग्राम", "mr": "ग्रॅम"},
    "kg": {"en": "kg", "hi": "किलो", "mr": "किलो"},
    "ml": {"en": "ml", "hi": "मि.ली.", "mr": "मि.ली."},
    "l": {"en": "l", "hi": "लीटर", "mr": "लिटर"},
    "piece": {"en": "pc", "hi": "नग", "mr": "नग"},
    "bunch": {"en": "bunch", "hi": "गड्डी", "mr": "जुडी"},
    "pack": {"en": "pack", "hi": "पैकेट", "mr": "पाकीट"},
}


def step_label(qty: float, unit: str, lang: Language) -> str:
    return f"{qty} {UNIT_LABELS[unit][lang]}"


def product_not_found() -> ProblemError:
    return ProblemError(404, "catalog.product_not_found", "No such product.")


# --- writing (seed now; the console editor later) ---------------------------------------------


@dataclass(frozen=True)
class CategoryData:
    slug: str
    names: dict[str, str]
    sort_order: int = 0


@dataclass(frozen=True)
class ProductData:
    slug: str
    kind: str
    category: str  # category slug
    names: dict[str, str]
    step_qty: int
    step_unit: str
    shelf_life_h: int | None = None
    tags: tuple[str, ...] = ()
    # Other spellings people use: English ("lady finger") and Hinglish ("bhendi", "pyaaz").
    synonyms: tuple[str, ...] = field(default=())
    data_source: str = "real"
    # Per 100 g (IFCT 2017), plus `unit_g` for products whose step is a piece or a bunch.
    nutrition: dict[str, Any] | None = None


async def save_category(session: AsyncSession, data: CategoryData) -> Category:
    category = (
        await session.execute(select(Category).where(Category.slug == data.slug))
    ).scalar_one_or_none()
    if category is None:
        category = Category(slug=data.slug)
        session.add(category)
    category.names = data.names
    category.sort_order = data.sort_order
    await session.flush()
    return category


async def save_product(session: AsyncSession, data: ProductData) -> tuple[Product, bool]:
    """Create or update the product with this slug and rebuild its search terms."""
    category = (
        await session.execute(select(Category).where(Category.slug == data.category))
    ).scalar_one()
    product = (
        await session.execute(select(Product).where(Product.slug == data.slug))
    ).scalar_one_or_none()
    created = product is None
    if product is None:
        product = Product(slug=data.slug)
        session.add(product)
    product.kind = data.kind
    product.category_id = category.id
    product.names = data.names
    product.step_qty = data.step_qty
    product.step_unit = data.step_unit
    product.shelf_life_h = data.shelf_life_h
    product.tags = list(data.tags)
    product.data_source = data.data_source
    if data.nutrition is not None:
        product.nutrition = data.nutrition
    await session.flush()
    await _rebuild_terms(session, product, data.synonyms)
    return product, created


def spellings(names: dict[str, str], synonyms: tuple[str, ...]) -> dict[str, tuple[str, str]]:
    """Every spelling of a product → (lang, source), first one wins on duplicates."""
    found: dict[str, tuple[str, str]] = {}

    def add(term: str, lang: str, source: str) -> None:
        term = " ".join(term.split())
        if term and term.lower() not in {t.lower() for t in found}:
            found[term] = (lang, source)

    for lang in ("en", "hi", "mr"):
        add(names[lang], lang, "name")
    for synonym in synonyms:
        add(synonym, "hinglish" if not has_devanagari(synonym) else "hi", "synonym")
    for lang in ("hi", "mr"):
        add(hinglish(names[lang]), "hinglish", "transliteration")
    return found


async def _rebuild_terms(
    session: AsyncSession, product: Product, synonyms: tuple[str, ...]
) -> None:
    await session.execute(
        SearchTerm.__table__.delete().where(SearchTerm.product_id == product.id)  # pyright: ignore[reportAttributeAccessIssue]
    )
    session.add_all(
        SearchTerm(product_id=product.id, term=term, lang=lang, source=source)
        for term, (lang, source) in spellings(product.names, synonyms).items()
    )
    await session.flush()


# --- reading ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class ProductRow:
    product: Product
    category: Category


async def get(session: AsyncSession, product_id: uuid.UUID) -> ProductRow:
    row = (
        await session.execute(
            select(Product, Category)
            .join(Category, Category.id == Product.category_id)
            .where(Product.id == product_id, Product.active)
        )
    ).first()
    if row is None:
        raise product_not_found()
    return ProductRow(row[0], row[1])


async def list_products(
    session: AsyncSession,
    *,
    kind: str | None,
    category: str | None,
    tag: str | None,
    cursor: str | None,
    limit: int,
) -> tuple[list[ProductRow], str | None]:
    """Active products, alphabetical by English name (keyset paginated)."""
    english = Product.names["en"].astext
    stmt = (
        select(Product, Category)
        .join(Category, Category.id == Product.category_id)
        .where(Product.active)
    )
    if kind:
        stmt = stmt.where(Product.kind == kind)
    if category:
        stmt = stmt.where(Category.slug == category)
    if tag:
        stmt = stmt.where(Product.tags.contains([tag]))
    if cursor is not None:
        after = decode_cursor(cursor).get("after")
        if not (isinstance(after, list) and len(after) == 2 and isinstance(after[0], str)):  # pyright: ignore[reportUnknownArgumentType]
            raise ProblemError(422, "request.invalid_cursor", "The cursor is not valid.")
        try:
            last_id = uuid.UUID(str(after[1]))  # pyright: ignore[reportUnknownArgumentType]
        except ValueError as exc:
            raise ProblemError(422, "request.invalid_cursor", "The cursor is not valid.") from exc
        stmt = stmt.where(tuple_(english, Product.id) > tuple_(after[0], last_id))
    rows = (await session.execute(stmt.order_by(english, Product.id).limit(limit + 1))).all()
    found = [ProductRow(row[0], row[1]) for row in rows[:limit]]
    next_cursor = None
    if len(rows) > limit:
        last = found[-1].product
        next_cursor = encode_cursor({"after": [last.names["en"], str(last.id)]})
    return found, next_cursor


@dataclass(frozen=True)
class Hit:
    product: Product
    matched: str
    score: float


async def search(session: AsyncSession, q: str, *, limit: int) -> list[Hit]:
    """Products by best-matching spelling, best first (one row per product)."""
    variants = [q] + ([hinglish(q)] if has_devanagari(q) else [])
    terms = [func.catalog.f_unaccent(func.lower(variant)) for variant in variants]
    # How well the query covers some part of a spelling (forgiving, good for partial words), then
    # how well it matches whole words (breaks ties: "bindi" is nearer "bhindi" than "phrench bins").
    score: Any = func.greatest(
        *(func.word_similarity(term, SearchTerm.term_norm, type_=Float) for term in terms)
    )
    strict: Any = func.greatest(
        *(func.strict_word_similarity(term, SearchTerm.term_norm, type_=Float) for term in terms)
    )
    # On a tie, report the spelling as typed ("भिंडी", not its Hinglish form).
    as_typed: Any = SearchTerm.term_norm == terms[0]
    await session.execute(
        text("SELECT set_config('pg_trgm.word_similarity_threshold', :t, true)"),
        {"t": str(MATCH_THRESHOLD)},
    )
    # The best spelling of each product (DISTINCT ON), among those close enough to the query.
    matches = (
        select(
            SearchTerm.product_id,
            SearchTerm.term,
            score.label("score"),
            strict.label("strict"),
        )
        .where(or_(*(term.op("<%")(SearchTerm.term_norm) for term in terms)))
        .order_by(
            SearchTerm.product_id, score.desc(), strict.desc(), as_typed.desc(), SearchTerm.term
        )
        .ext(distinct_on(SearchTerm.product_id))
        .subquery()
    )
    rows = (
        await session.execute(
            select(Product, matches.c.term, matches.c.score)
            .join(matches, matches.c.product_id == Product.id)
            .where(Product.active)
            .order_by(
                matches.c.score.desc(),
                matches.c.strict.desc(),
                Product.names["en"].astext,
                Product.id,
            )
            .limit(limit)
        )
    ).all()
    return [Hit(row[0], row[1], float(row[2])) for row in rows]
