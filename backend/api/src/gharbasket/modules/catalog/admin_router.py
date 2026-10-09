"""HTTP endpoints of the console's catalog editor (spec §3.3): categories and products.

Admins, hub managers and catalog editors read the admin lists (`catalog:admin_read`). A read of one
product sends its version as the ETag, for the edits that come with If-Match.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, Response

from gharbasket.modules.catalog import admin
from gharbasket.modules.catalog import permissions as _permissions  # noqa: F401 (registers rules)
from gharbasket.modules.catalog.schemas import AdminProduct, Categories, CategoryOut, Kind
from gharbasket.modules.catalog.service import ProductRow, step_label
from gharbasket.modules.identity import api as identity_api
from gharbasket.platform.auth import CurrentPrincipal
from gharbasket.platform.db import DbSession
from gharbasket.platform.etag import set_etag
from gharbasket.platform.locale import Lang, Language, localised
from gharbasket.platform.pagination import Cursor, Page
from gharbasket.platform.permissions import Permit, require

router = APIRouter(tags=["catalog"])

PRODUCTS = "/admin/products"
CanRead = Annotated[Permit, require("catalog:admin_read")]


def _problem(description: str) -> dict[str, Any]:
    return {"description": description, "content": {"application/problem+json": {}}}


@router.get("/categories", summary="Product categories, in their display order")
async def list_categories(
    _principal: CurrentPrincipal, session: DbSession, lang: Lang, response: Response
) -> Categories:
    found = await admin.categories(session)
    response.headers["Cache-Control"] = "private, max-age=300"
    return Categories(
        items=[
            CategoryOut.model_validate({**c.as_dict(), "name": localised(c.names, lang)})
            for c in found
        ]
    )


async def _admin_out(
    session: DbSession, rows: list[ProductRow], lang: Language
) -> list[AdminProduct]:
    ids = [row.product.id for row in rows]
    terms, used = await admin.synonyms(session, ids), await admin.used_by_recipes(session, ids)
    editors = await identity_api.user_names(
        session, {row.product.updated_by for row in rows if row.product.updated_by}
    )
    out: list[AdminProduct] = []
    for row in rows:
        p, c = row.product, row.category
        label = step_label(p.step_qty, p.step_unit, lang)
        editor = p.updated_by and {"id": p.updated_by, "name": editors.get(p.updated_by)}
        out.append(
            AdminProduct.model_validate(
                {
                    **p.as_dict(),
                    "category": {"id": c.id, "slug": c.slug, "name": localised(c.names, lang)},
                    "name": localised(p.names, lang),
                    "step": {"qty": p.step_qty, "unit": p.step_unit, "label": label},
                    "image_url": None,
                    "nutrition": admin.nutrition_view(p.nutrition),
                    "synonyms": terms[p.id],
                    "used_by_recipes": used.get(p.id, 0),
                    "updated_by": editor,
                }
            )
        )
    return out


@router.get(
    PRODUCTS,
    summary="Every product for the console, active or not, alphabetical",
    responses={403: _problem("auth.forbidden")},
)
async def list_admin_products(
    _permit: CanRead,
    session: DbSession,
    lang: Lang,
    response: Response,
    q: Annotated[str | None, Query(max_length=80, description="In the slug or a name")] = None,
    kind: Kind | None = None,
    category: Annotated[str | None, Query(max_length=60, description="Category slug")] = None,
    active: Annotated[bool | None, Query(description="Leave out for both")] = None,
    cursor: Cursor = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> Page[AdminProduct]:
    rows, next_cursor = await admin.list_products(
        session, q=q, kind=kind, category=category, active=active, cursor=cursor, limit=limit
    )
    response.headers["Cache-Control"] = "no-store"
    return Page(items=await _admin_out(session, rows, lang), next_cursor=next_cursor)


@router.get(
    PRODUCTS + "/{product_id}",
    summary="One product for the console (ETag: its version)",
    responses={403: _problem("auth.forbidden"), 404: _problem("catalog.product_not_found")},
)
async def get_admin_product(
    product_id: uuid.UUID, _permit: CanRead, session: DbSession, lang: Lang, response: Response
) -> AdminProduct:
    row = await admin.get(session, product_id)
    set_etag(response, row.product.version)
    response.headers["Cache-Control"] = "no-store"
    return (await _admin_out(session, [row], lang))[0]
