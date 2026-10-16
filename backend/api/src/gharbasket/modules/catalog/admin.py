"""The console's product editor (spec §3.3): create, edit, deactivate, history.

Every write sets `updated_by` (so `just seed` leaves the row alone from then on), bumps `version`,
is audit-logged with the changed fields before and after, and publishes `catalog.product_changed`.
A change to a product's tags recalculates the derived rules of every recipe using it, in the same
transaction. History is read back from the audit log.
"""

import uuid
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog import events
from gharbasket.modules.catalog.models import (
    NUTRITION_SOURCES,
    TAGS,
    Category,
    Product,
    Recipe,
    RecipeIngredient,
    SearchTerm,
)
from gharbasket.modules.catalog.recipes import recompute_rules
from gharbasket.modules.catalog.schemas import NamesIn, ProductIn, ProductPatch
from gharbasket.modules.catalog.service import (
    ProductRow,
    page_by_name,
    product_not_found,
    rebuild_terms,
)
from gharbasket.platform import audit
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.models import AuditEntry
from gharbasket.platform.outbox import publish

# Kits come from recipes and tiffins from kitchens' menus, each in their own flow.
EDITABLE_KINDS = ("veg", "fruit", "staple", "salad")
IMMUTABLE = ("slug", "kind", "step")
NULLABLE = ("shelf_life_h", "nutrition")
HISTORY_LIMIT = 100
NUTRITION_FIELDS = ("kcal", "protein_g", "fat_g", "carb_g", "fibre_g", "unit_g")


def nutrition_view(raw: dict[str, Any] | None) -> dict[str, Any] | None:
    """The §3.3 fields of a product's nutrition (rows seeded before §3.3 hold a source object)."""
    if not raw:
        return None
    source = raw.get("source")
    if isinstance(source, dict):
        source = NUTRITION_SOURCES.get(str(source.get("name")), "manual")  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]
    return {**{key: raw[key] for key in NUTRITION_FIELDS if key in raw}, "source": source}


async def categories(session: AsyncSession) -> list[Category]:
    rows = await session.execute(select(Category).order_by(Category.sort_order, Category.slug))
    return list(rows.scalars())


async def list_products(
    session: AsyncSession,
    *,
    q: str | None,
    kind: str | None,
    category: str | None,
    active: bool | None,
    cursor: str | None,
    limit: int,
) -> tuple[list[ProductRow], str | None]:
    stmt = select(Product, Category).join(Category, Category.id == Product.category_id)
    if q:
        names = (Product.names[lang].astext for lang in ("en", "hi", "mr"))
        stmt = stmt.where(
            or_(*(column.icontains(q, autoescape=True) for column in (Product.slug, *names)))
        )
    if kind:
        stmt = stmt.where(Product.kind == kind)
    if category:
        stmt = stmt.where(Category.slug == category)
    if active is not None:
        stmt = stmt.where(Product.active == active)
    return await page_by_name(session, stmt, cursor, limit)


async def get(session: AsyncSession, product_id: uuid.UUID, *, lock: bool = False) -> ProductRow:
    """A product whether active or not (the console sees both)."""
    stmt = (
        select(Product, Category)
        .join(Category, Category.id == Product.category_id)
        .where(Product.id == product_id)
    )
    row = (await session.execute(stmt.with_for_update(of=Product) if lock else stmt)).first()
    if row is None:
        raise product_not_found()
    return ProductRow(row[0], row[1])


async def synonyms(
    session: AsyncSession, product_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[dict[str, str]]]:
    found: dict[uuid.UUID, list[dict[str, str]]] = {product_id: [] for product_id in product_ids}
    rows = await session.execute(
        select(SearchTerm.product_id, SearchTerm.term, SearchTerm.lang)
        .where(SearchTerm.product_id.in_(product_ids), SearchTerm.source == "synonym")
        .order_by(SearchTerm.id)
    )
    for product_id, term, lang in rows.all():
        found[product_id].append({"term": term, "lang": lang})
    return found


async def used_by_recipes(
    session: AsyncSession, product_ids: list[uuid.UUID]
) -> dict[uuid.UUID, int]:
    rows = await session.execute(
        select(RecipeIngredient.product_id, func.count())
        .join(Recipe, Recipe.id == RecipeIngredient.recipe_id)
        .where(RecipeIngredient.product_id.in_(product_ids), Recipe.active)
        .group_by(RecipeIngredient.product_id)
    )
    return {product_id: count for product_id, count in rows.all()}


def checked_names(names: NamesIn) -> dict[str, str]:
    given = {lang: " ".join((getattr(names, lang) or "").split()) for lang in ("en", "hi", "mr")}
    if missing := [lang for lang, name in given.items() if not name]:
        raise ProblemError(
            422,
            "catalog.names_incomplete",
            "A name is needed in English, Hindi and Marathi.",
            extra={"missing": missing},
        )
    return given


def _tags(tags: list[str]) -> list[str]:
    if unknown := sorted(set(tags) - set(TAGS)):
        raise ProblemError(422, "catalog.tag_unknown", "Unknown tags.", extra={"tags": unknown})
    return list(dict.fromkeys(tags))


async def _category(session: AsyncSession, category_id: uuid.UUID) -> uuid.UUID:
    if await session.get(Category, category_id) is None:
        raise ProblemError(422, "catalog.category_unknown", "No such category.")
    return category_id


def _snapshot(product: Product, terms: list[dict[str, str]]) -> dict[str, Any]:
    """The editable fields as they are saved in the audit log."""
    return {
        "slug": product.slug,
        "kind": product.kind,
        "step": {"qty": product.step_qty, "unit": product.step_unit},
        "category_id": str(product.category_id),
        "names": product.names,
        "shelf_life_h": product.shelf_life_h,
        "tags": product.tags,
        "nutrition": product.nutrition,
        "synonyms": terms,
        "active": product.active,
    }


def _slug_taken() -> ProblemError:
    return ProblemError(409, "catalog.slug_taken", "Another product has this slug.")


async def create(session: AsyncSession, body: ProductIn, actor: uuid.UUID) -> Product:
    if body.kind not in EDITABLE_KINDS:
        raise ProblemError(
            422,
            "catalog.kind_not_editable",
            "The console creates vegetables, fruits, staples and salads.",
        )
    names, tags = checked_names(body.names), _tags(body.tags)
    category_id = await _category(session, body.category_id)
    if await session.scalar(select(Product.id).where(Product.slug == body.slug)):
        raise _slug_taken()
    product = Product(
        slug=body.slug,
        kind=body.kind,
        category_id=category_id,
        names=names,
        step_qty=body.step.qty,
        step_unit=body.step.unit,
        shelf_life_h=body.shelf_life_h,
        tags=tags,
        nutrition=body.nutrition.model_dump() if body.nutrition else None,
        active=True,
        data_source="real",
        version=1,
        updated_by=actor,
    )
    try:
        async with session.begin_nested():  # two editors saving the same slug at once
            session.add(product)
            await session.flush()
    except IntegrityError:
        raise _slug_taken() from None
    await rebuild_terms(session, product, [(s.term, s.lang) for s in body.synonyms])
    after = _snapshot(product, (await synonyms(session, [product.id]))[product.id])
    await _record(session, product, actor, "created", None, after)
    return product


async def update(
    session: AsyncSession,
    product_id: uuid.UUID,
    expected: int,
    body: ProductPatch,
    actor: uuid.UUID,
) -> tuple[Product, list[str]]:
    """Apply the fields sent; returns the product and the recipes whose rules changed."""
    sent = body.model_fields_set
    if immutable := [name for name in IMMUTABLE if name in sent]:
        raise ProblemError(
            422,
            "catalog.field_immutable",
            "Slug, kind and step never change: create a new product and deactivate this one.",
            extra={"fields": immutable},
        )
    if nulls := sorted(n for n in sent if getattr(body, n) is None and n not in NULLABLE):
        raise ProblemError(422, "request.invalid", f"These cannot be null: {', '.join(nulls)}.")
    product = (await get(session, product_id, lock=True)).product
    if product.version != expected:
        raise ProblemError(
            412, "catalog.version_conflict", "Someone else saved this product; reload it."
        )
    terms = (await synonyms(session, [product.id]))[product.id]
    before = _snapshot(product, terms)

    if body.names is not None:
        product.names = checked_names(body.names)
    if body.tags is not None:
        product.tags = _tags(body.tags)
    if body.category_id is not None:
        product.category_id = await _category(session, body.category_id)
    if "shelf_life_h" in sent:
        product.shelf_life_h = body.shelf_life_h
    if "nutrition" in sent:
        product.nutrition = body.nutrition.model_dump() if body.nutrition else None
    if body.synonyms is not None:
        terms = [s.model_dump() for s in body.synonyms]
    if body.active is False and product.active:
        await _ensure_unused(session, product)
    if body.active is not None:
        product.active = body.active

    after = _snapshot(product, terms)
    changed = [name for name in after if after[name] != before[name]]
    if not changed:
        return product, []  # nothing to save: same version, no audit entry
    product.version += 1
    product.updated_by = actor
    await session.flush()
    if {"names", "synonyms"} & set(changed):
        await rebuild_terms(session, product, [(t["term"], t["lang"]) for t in terms])
        after["synonyms"] = (await synonyms(session, [product.id]))[product.id]
    recomputed = await recompute_rules(session, [product.id], actor) if "tags" in changed else []
    await _record(
        session,
        product,
        actor,
        "updated",
        {name: before[name] for name in changed},
        {name: after[name] for name in changed},
    )
    return product, recomputed


async def _ensure_unused(session: AsyncSession, product: Product) -> None:
    needed_by = list(
        (
            await session.execute(
                select(Recipe.slug)
                .join(RecipeIngredient, RecipeIngredient.recipe_id == Recipe.id)
                .where(
                    RecipeIngredient.product_id == product.id,
                    ~RecipeIngredient.optional,
                    Recipe.active,
                )
                .order_by(Recipe.slug)
            )
        ).scalars()
    )
    if needed_by:
        raise ProblemError(
            409,
            "catalog.product_in_use",
            "Active recipes need this product; change or deactivate them first.",
            extra={"recipes": needed_by},
        )


async def _record(
    session: AsyncSession,
    product: Product,
    actor: uuid.UUID,
    verb: str,
    before: dict[str, Any] | None,
    after: dict[str, Any],
) -> None:
    await audit.record(
        session,
        actor_user_id=actor,
        action=f"catalog.product_{verb}",
        entity_type="product",
        entity_id=str(product.id),
        before=before,
        after=after,
    )
    await publish(
        session,
        events.PRODUCT_CHANGED,
        {"product_id": str(product.id), "changed": sorted(after), "active": product.active},
    )
    await session.refresh(product)  # server-side updated_at


async def history(
    session: AsyncSession, entity_type: str, entity_id: uuid.UUID
) -> list[AuditEntry]:
    """The audit entries of one catalog row, newest first."""
    rows = await session.execute(
        select(AuditEntry)
        .where(AuditEntry.entity_type == entity_type, AuditEntry.entity_id == str(entity_id))
        .order_by(AuditEntry.at.desc(), AuditEntry.id.desc())
        .limit(HISTORY_LIMIT)
    )
    return list(rows.scalars())
