"""The console's recipe editor (spec §3.3): create, edit, deactivate, history.

A save recalculates the derived rules (diet, jain_ok, no_onion_garlic) from the ingredients, sets
`updated_by` (so `just seed` leaves the recipe alone from then on), bumps `version`, is audit-logged
with the changed fields before and after, and publishes `catalog.recipe_changed`. A change to the
curated `fasting_ok` or `diabetic_ok` records who made it and when (`reviews`). Catalog editors may
change only the names and the curated flags.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog import events
from gharbasket.modules.catalog.admin import checked_names
from gharbasket.modules.catalog.models import Product, Recipe, RecipeIngredient
from gharbasket.modules.catalog.recipes import (
    BASE_UNIT,
    Ingredient,
    derived_rules,
    page_recipes,
    recipe_not_found,
)
from gharbasket.modules.catalog.schemas import RecipeIn, RecipePatch
from gharbasket.platform import audit
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.outbox import publish

# What a catalog editor may change (`recipes:curate`); every other field needs `catalog:write`.
CURATED = frozenset({"names", "fasting_ok", "diabetic_ok"})
NULLABLE = ("steps",)
# A line of a recipe: (product id, qty per serving, unit, optional).
Line = tuple[uuid.UUID, Decimal, str, bool]


async def list_recipes(
    session: AsyncSession,
    *,
    q: str | None,
    cuisine: str | None,
    slot: str | None,
    active: bool | None,
    is_side: bool | None,
    cursor: str | None,
    limit: int,
) -> tuple[list[Recipe], str | None]:
    stmt = select(Recipe)
    if q:
        names = (Recipe.names[lang].astext for lang in ("en", "hi", "mr"))
        stmt = stmt.where(
            or_(*(column.icontains(q, autoescape=True) for column in (Recipe.slug, *names)))
        )
    if cuisine:
        stmt = stmt.where(Recipe.cuisine == cuisine)
    if slot:
        stmt = stmt.where(Recipe.slots.contains([slot]))
    if active is not None:
        stmt = stmt.where(Recipe.active == active)
    if is_side is not None:
        stmt = stmt.where(Recipe.is_side == is_side)
    return await page_recipes(session, stmt, cursor, limit)


async def get(session: AsyncSession, recipe_id: uuid.UUID, *, lock: bool = False) -> Recipe:
    """A recipe whether active or not (the console sees both)."""
    stmt = select(Recipe).where(Recipe.id == recipe_id)
    recipe = (await session.execute(stmt.with_for_update() if lock else stmt)).scalar_one_or_none()
    if recipe is None:
        raise recipe_not_found()
    return recipe


async def ingredients(
    session: AsyncSession, recipe_ids: list[uuid.UUID]
) -> dict[uuid.UUID, list[Ingredient]]:
    found: dict[uuid.UUID, list[Ingredient]] = {recipe_id: [] for recipe_id in recipe_ids}
    rows = await session.execute(
        select(RecipeIngredient, Product)
        .join(Product, Product.id == RecipeIngredient.product_id)
        .where(RecipeIngredient.recipe_id.in_(recipe_ids))
        .order_by(RecipeIngredient.optional, Product.names["en"].astext)
    )
    for link, product in rows.all():
        found[link.recipe_id].append(
            Ingredient(product, link.qty_per_serving, link.unit, link.optional)
        )
    return found


async def _checked_lines(session: AsyncSession, lines: list[Line]) -> list[Product]:
    """The products of these lines: all active, each measured in the unit of its step."""
    wanted = [line[0] for line in lines]
    products = {
        p.id: p
        for p in (
            await session.execute(select(Product).where(Product.id.in_(wanted), Product.active))
        ).scalars()
    }
    if unknown := sorted(str(product_id) for product_id in set(wanted) - set(products)):
        raise ProblemError(
            422,
            "catalog.product_unknown",
            "Some ingredients do not exist or are not on sale.",
            extra={"product_ids": unknown},
        )
    if mismatched := [
        str(product_id)
        for product_id, _, unit, _ in lines
        if BASE_UNIT[products[product_id].step_unit][0] != unit
    ]:
        raise ProblemError(
            422,
            "catalog.unit_mismatch",
            "Use g for products sold by weight, ml by volume, piece by piece or pack, bunch by "
            "bunch.",
            extra={"product_ids": mismatched},
        )
    return list(products.values())


async def _check_sides(session: AsyncSession, slug: str, is_side: bool, sides: list[str]) -> None:
    if not sides:
        return
    if is_side:
        raise ProblemError(422, "catalog.side_invalid", "A side has no sides of its own.")
    known = set(
        (
            await session.execute(
                select(Recipe.slug).where(Recipe.slug.in_(sides), Recipe.is_side, Recipe.active)
            )
        ).scalars()
    )
    if unknown := sorted((set(sides) - known) | ({slug} & set(sides))):
        raise ProblemError(
            422,
            "catalog.side_invalid",
            "Sides must be active side recipes.",
            extra={"sides": unknown},
        )


async def _ensure_unlisted(session: AsyncSession, recipe: Recipe) -> None:
    """Active recipes that list this one as a side keep needing it."""
    listed_by = list(
        (
            await session.execute(
                select(Recipe.slug)
                .where(Recipe.active, Recipe.sides.contains([recipe.slug]), Recipe.id != recipe.id)
                .order_by(Recipe.slug)
            )
        ).scalars()
    )
    if listed_by:
        raise ProblemError(
            409,
            "catalog.recipe_in_use",
            "Active recipes list this side; change or deactivate them first.",
            extra={"recipes": listed_by},
        )


def _lines_view(lines: list[Line]) -> list[dict[str, Any]]:
    return sorted(
        (
            {"product_id": str(p), "qty": float(qty), "unit": unit, "optional": optional}
            for p, qty, unit, optional in lines
        ),
        key=lambda line: line["product_id"],
    )


def _snapshot(recipe: Recipe, lines: list[Line]) -> dict[str, Any]:
    """The editable fields as they are saved in the audit log."""
    return {
        "slug": recipe.slug,
        "names": recipe.names,
        "cuisine": recipe.cuisine,
        "slots": recipe.slots,
        "cook_minutes": recipe.cook_minutes,
        "servings": recipe.servings,
        "tags": recipe.tags,
        "is_side": recipe.is_side,
        "sides": recipe.sides,
        "diet": recipe.diet,
        "rules": recipe.rules,
        "ingredients": _lines_view(lines),
        "steps": recipe.steps,
        "active": recipe.active,
    }


def _lines(items: Any) -> list[Line]:
    return [(i.product_id, i.qty, i.unit, i.optional) for i in items]


def _review(recipe: Recipe, flag: str, value: bool, actor: uuid.UUID, now: datetime) -> None:
    """Set a curated flag, recording who changed it and when."""
    if recipe.rules.get(flag) == value:
        return
    recipe.rules = {**recipe.rules, flag: value}
    name = flag.removesuffix("_ok")
    setattr(recipe, f"{name}_reviewed_by", actor)
    setattr(recipe, f"{name}_reviewed_at", now)


async def _write_lines(session: AsyncSession, recipe: Recipe, lines: list[Line]) -> None:
    await session.execute(delete(RecipeIngredient).where(RecipeIngredient.recipe_id == recipe.id))
    session.add_all(
        RecipeIngredient(
            recipe_id=recipe.id,
            product_id=product_id,
            qty_per_serving=qty,
            unit=unit,
            optional=optional,
        )
        for product_id, qty, unit, optional in lines
    )
    await session.flush()


def _derive(recipe: Recipe, products: list[Product]) -> None:
    diet, jain_ok, no_onion_garlic = derived_rules([set(p.tags) for p in products])
    recipe.diet = diet
    recipe.rules = {**recipe.rules, "jain_ok": jain_ok, "no_onion_garlic": no_onion_garlic}


def _slug_taken() -> ProblemError:
    return ProblemError(409, "catalog.slug_taken", "Another recipe has this slug.")


async def create(session: AsyncSession, body: RecipeIn, actor: uuid.UUID) -> Recipe:
    names, lines = checked_names(body.names), _lines(body.ingredients)
    products = await _checked_lines(session, lines)
    sides = list(dict.fromkeys(body.sides))
    await _check_sides(session, body.slug, body.is_side, sides)
    if await session.scalar(select(Recipe.id).where(Recipe.slug == body.slug)):
        raise _slug_taken()
    now = datetime.now(UTC)
    recipe = Recipe(
        slug=body.slug,
        names=names,
        cuisine=body.cuisine,
        slots=list(dict.fromkeys(body.slots)),
        cook_minutes=body.cook_minutes,
        servings=body.servings,
        tags=list(dict.fromkeys(body.tags)),
        is_side=body.is_side,
        sides=sides,
        steps=[s.model_dump(exclude_none=True) for s in body.steps] if body.steps else None,
        rules={"fasting_ok": body.fasting_ok, "diabetic_ok": body.diabetic_ok},
        active=True,
        data_source="real",
        version=1,
        updated_by=actor,
        fasting_reviewed_by=actor,
        fasting_reviewed_at=now,
        diabetic_reviewed_by=actor,
        diabetic_reviewed_at=now,
    )
    _derive(recipe, products)
    try:
        async with session.begin_nested():  # two editors saving the same slug at once
            session.add(recipe)
            await session.flush()
    except IntegrityError:
        raise _slug_taken() from None
    await _write_lines(session, recipe, lines)
    await _record(session, recipe, actor, "created", None, _snapshot(recipe, lines))
    return recipe


async def update(
    session: AsyncSession,
    recipe_id: uuid.UUID,
    expected: int,
    body: RecipePatch,
    actor: uuid.UUID,
    *,
    editor_only: bool,
) -> Recipe:
    """Apply the fields sent. `editor_only`: the caller may change only the curated fields."""
    sent = body.model_fields_set
    if "slug" in sent:
        raise ProblemError(
            422,
            "catalog.field_immutable",
            "A slug never changes: create a new recipe and deactivate this one.",
            extra={"fields": ["slug"]},
        )
    if editor_only and (others := sorted(sent - CURATED)):
        raise ProblemError(
            403,
            "auth.forbidden",
            "A catalog editor changes only a recipe's names and its fasting and diabetic flags.",
            extra={"fields": others},
        )
    if nulls := sorted(n for n in sent if getattr(body, n) is None and n not in NULLABLE):
        raise ProblemError(422, "request.invalid", f"These cannot be null: {', '.join(nulls)}.")
    recipe = await get(session, recipe_id, lock=True)
    if recipe.version != expected:
        raise ProblemError(
            412, "catalog.version_conflict", "Someone else saved this recipe; reload it."
        )
    current = [
        (i.product.id, i.qty, i.unit, i.optional)
        for i in (await ingredients(session, [recipe.id]))[recipe.id]
    ]
    before = _snapshot(recipe, current)
    reactivated = body.active is True and not recipe.active

    if body.names is not None:
        recipe.names = checked_names(body.names)
    for field in ("cuisine", "cook_minutes", "servings"):
        if field in sent:
            setattr(recipe, field, getattr(body, field))
    if body.slots is not None:
        recipe.slots = list(dict.fromkeys(body.slots))
    if body.tags is not None:
        recipe.tags = list(dict.fromkeys(body.tags))
    if "steps" in sent:
        recipe.steps = [s.model_dump(exclude_none=True) for s in body.steps or []] or None
    lines = _lines(body.ingredients) if body.ingredients is not None else current
    if body.ingredients is not None or reactivated:
        _derive(recipe, await _checked_lines(session, lines))
    if (body.is_side is False and recipe.is_side) or (body.active is False and recipe.active):
        await _ensure_unlisted(session, recipe)
    if body.is_side is not None:
        recipe.is_side = body.is_side
    if body.sides is not None:
        recipe.sides = list(dict.fromkeys(body.sides))
    if {"is_side", "sides"} & sent or reactivated:
        await _check_sides(session, recipe.slug, recipe.is_side, recipe.sides)
    if body.active is not None:
        recipe.active = body.active
    now = datetime.now(UTC)
    for flag in ("fasting_ok", "diabetic_ok"):
        if (value := getattr(body, flag)) is not None:
            _review(recipe, flag, value, actor, now)

    after = _snapshot(recipe, lines)
    changed = [name for name in after if after[name] != before[name]]
    if not changed:
        return recipe  # nothing to save: same version, no audit entry
    recipe.version += 1
    recipe.updated_by = actor
    await session.flush()
    if "ingredients" in changed:
        await _write_lines(session, recipe, lines)
    await _record(
        session,
        recipe,
        actor,
        "updated",
        {name: before[name] for name in changed},
        {name: after[name] for name in changed},
    )
    return recipe


async def _record(
    session: AsyncSession,
    recipe: Recipe,
    actor: uuid.UUID,
    verb: str,
    before: dict[str, Any] | None,
    after: dict[str, Any],
) -> None:
    await audit.record(
        session,
        actor_user_id=actor,
        action=f"catalog.recipe_{verb}",
        entity_type="recipe",
        entity_id=str(recipe.id),
        before=before,
        after=after,
    )
    await publish(
        session,
        events.RECIPE_CHANGED,
        {"recipe_id": str(recipe.id), "changed": sorted(after), "active": recipe.active},
    )
    await session.refresh(recipe)  # server-side updated_at
