"""HTTP endpoints of the console's recipe editor (spec §3.3).

Admins write (`catalog:write`); catalog editors change a recipe's names and its fasting and
diabetic flags (`recipes:curate`); admins, hub managers and catalog editors read
(`catalog:admin_read`). Reads of one recipe send its version as the ETag; edits need If-Match.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, Response, status

from gharbasket.modules.catalog import permissions as _permissions  # noqa: F401 (registers rules)
from gharbasket.modules.catalog import recipe_admin
from gharbasket.modules.catalog.admin_router import CanRead, CanWrite, history_out, problem
from gharbasket.modules.catalog.models import Recipe
from gharbasket.modules.catalog.schemas import (
    AdminRecipe,
    Cuisine,
    History,
    RecipeIn,
    RecipePatch,
    Slot,
)
from gharbasket.modules.catalog.service import step_label
from gharbasket.modules.identity import api as identity_api
from gharbasket.platform.db import DbSession
from gharbasket.platform.etag import IfMatch, set_etag
from gharbasket.platform.idempotency import Idempotent
from gharbasket.platform.locale import Lang, Language, localised
from gharbasket.platform.pagination import Cursor, Page
from gharbasket.platform.permissions import PERMISSIONS, Permit, require

router = APIRouter(tags=["catalog"])

RECIPES = "/admin/recipes"
CanCurate = Annotated[Permit, require("recipes:curate")]


def _number(qty: Any) -> Any:
    """50.00 → 50, 0.50 → 0.5 (for labels)."""
    return int(qty) if qty == int(qty) else float(qty)


async def _out(
    session: DbSession, found: list[Recipe], lang: Language, *, steps: bool
) -> list[AdminRecipe]:
    lines = await recipe_admin.ingredients(session, [r.id for r in found])
    people = await identity_api.user_names(
        session,
        {
            person
            for r in found
            for person in (r.updated_by, r.fasting_reviewed_by, r.diabetic_reviewed_by)
            if person
        },
    )

    def person(user_id: uuid.UUID | None) -> dict[str, Any] | None:
        return user_id and {"id": user_id, "name": people.get(user_id)}

    def review(by: uuid.UUID | None, at: Any) -> dict[str, Any] | None:
        return at and {"by": person(by), "at": at}

    return [
        AdminRecipe.model_validate(
            {
                **r.as_dict(),
                "name": localised(r.names, lang),
                "ingredients": [
                    {
                        "product_id": i.product.id,
                        "slug": i.product.slug,
                        "name": localised(i.product.names, lang),
                        "qty": float(i.qty),
                        "unit": i.unit,
                        "label": step_label(_number(i.qty), i.unit, lang),
                        "optional": i.optional,
                    }
                    for i in lines[r.id]
                ],
                "steps": r.steps if steps else None,
                "reviews": {
                    "fasting_ok": review(r.fasting_reviewed_by, r.fasting_reviewed_at),
                    "diabetic_ok": review(r.diabetic_reviewed_by, r.diabetic_reviewed_at),
                },
                "updated_by": person(r.updated_by),
            }
        )
        for r in found
    ]


@router.get(
    RECIPES,
    summary="Every recipe for the console, active or not, alphabetical (without steps)",
    responses={403: problem("auth.forbidden")},
)
async def list_admin_recipes(
    _permit: CanRead,
    session: DbSession,
    lang: Lang,
    response: Response,
    q: Annotated[str | None, Query(max_length=80, description="In the slug or a name")] = None,
    cuisine: Cuisine | None = None,
    slot: Slot | None = None,
    active: Annotated[bool | None, Query(description="Leave out for both")] = None,
    is_side: bool | None = None,
    cursor: Cursor = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> Page[AdminRecipe]:
    found, next_cursor = await recipe_admin.list_recipes(
        session,
        q=q,
        cuisine=cuisine,
        slot=slot,
        active=active,
        is_side=is_side,
        cursor=cursor,
        limit=limit,
    )
    response.headers["Cache-Control"] = "no-store"
    return Page(items=await _out(session, found, lang, steps=False), next_cursor=next_cursor)


@router.get(
    RECIPES + "/{recipe_id}",
    summary="One recipe for the console (ETag: its version)",
    responses={403: problem("auth.forbidden"), 404: problem("catalog.recipe_not_found")},
)
async def get_admin_recipe(
    recipe_id: uuid.UUID, _permit: CanRead, session: DbSession, lang: Lang, response: Response
) -> AdminRecipe:
    recipe = await recipe_admin.get(session, recipe_id)
    set_etag(response, recipe.version)
    response.headers["Cache-Control"] = "no-store"
    return (await _out(session, [recipe], lang, steps=True))[0]


@router.post(
    RECIPES,
    status_code=status.HTTP_201_CREATED,
    summary="Add a recipe (admin; Idempotency-Key; audit-logged)",
    responses={
        403: problem("auth.forbidden"),
        409: problem("catalog.slug_taken"),
        422: problem(
            "catalog.names_incomplete (missing), catalog.product_unknown (product_ids), "
            "catalog.unit_mismatch (product_ids), catalog.side_invalid (sides), request.invalid"
        ),
    },
)
async def create_recipe(
    body: RecipeIn,
    permit: CanWrite,
    session: DbSession,
    idem: Idempotent,
    lang: Lang,
    response: Response,
) -> AdminRecipe:
    recipe = await recipe_admin.create(session, body, permit.principal.user_id)
    [out] = await _out(session, [recipe], lang, steps=True)
    await idem.save(201, out)
    await session.commit()
    set_etag(response, recipe.version)
    return out


@router.patch(
    RECIPES + "/{recipe_id}",
    summary=(
        "Edit a recipe, or deactivate or reactivate it (admin; a catalog editor changes only the "
        "names and the fasting and diabetic flags; If-Match; audit-logged)"
    ),
    responses={
        403: problem("auth.forbidden (fields: what a catalog editor may not change)"),
        404: problem("catalog.recipe_not_found"),
        409: problem("catalog.recipe_in_use (recipes)"),
        412: problem("catalog.version_conflict"),
        422: problem(
            "catalog.field_immutable (fields), catalog.names_incomplete (missing), "
            "catalog.product_unknown (product_ids), catalog.unit_mismatch (product_ids), "
            "catalog.side_invalid (sides), request.invalid"
        ),
        428: problem("request.precondition_required"),
    },
)
async def edit_recipe(
    recipe_id: uuid.UUID,
    body: RecipePatch,
    permit: CanCurate,
    session: DbSession,
    expected: IfMatch,
    lang: Lang,
    response: Response,
) -> AdminRecipe:
    writers = PERMISSIONS.roles_for("catalog:write")
    editor_only = not any(grant.role in writers for grant in permit.grants)
    recipe = await recipe_admin.update(
        session, recipe_id, expected, body, permit.principal.user_id, editor_only=editor_only
    )
    [out] = await _out(session, [recipe], lang, steps=True)
    await session.commit()
    set_etag(response, recipe.version)
    return out


@router.get(
    RECIPES + "/{recipe_id}/history",
    summary="Who changed a recipe and what, newest first (from the audit log)",
    responses={403: problem("auth.forbidden"), 404: problem("catalog.recipe_not_found")},
)
async def recipe_history(recipe_id: uuid.UUID, _permit: CanRead, session: DbSession) -> History:
    await recipe_admin.get(session, recipe_id)
    return await history_out(session, "recipe", recipe_id)
