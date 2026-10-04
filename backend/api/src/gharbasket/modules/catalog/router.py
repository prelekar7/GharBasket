"""HTTP endpoints of M2 Catalog & Pricing: products and multilingual search (spec §3.1).

Prices are added to these answers with the price lists (next PR); until then `price` is null.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query, Response

from gharbasket.modules.catalog import service
from gharbasket.modules.catalog.schemas import (
    CategoryRef,
    Kind,
    Names,
    ProductOut,
    SearchItem,
    SearchResults,
    Step,
)
from gharbasket.platform.auth import CurrentPrincipal
from gharbasket.platform.db import DbSession
from gharbasket.platform.locale import Lang, Language, localised
from gharbasket.platform.pagination import Cursor, Page

router = APIRouter(tags=["catalog"])

# Products change rarely; the PWA's service worker caches them per language (spec §3.1).
CATALOG_CACHE = "private, max-age=300"


def _problem(description: str) -> dict[str, Any]:
    return {"description": description, "content": {"application/problem+json": {}}}


def _step(qty: int, unit: str, lang: Language) -> Step:
    return Step(qty=qty, unit=unit, label=service.step_label(qty, unit, lang))  # pyright: ignore[reportArgumentType]


def _product_out(row: service.ProductRow, lang: Language) -> ProductOut:
    product, category = row.product, row.category
    return ProductOut(
        id=product.id,
        slug=product.slug,
        kind=product.kind,  # pyright: ignore[reportArgumentType]
        category=CategoryRef(
            id=category.id, slug=category.slug, name=localised(category.names, lang)
        ),
        name=localised(product.names, lang),
        names=Names.model_validate(product.names),
        step=_step(product.step_qty, product.step_unit, lang),
        tags=product.tags,
        shelf_life_h=product.shelf_life_h,
        image_url=None,
        price=None,
    )


@router.get(
    "/products",
    summary="Products, alphabetical (names in the Accept-Language language)",
    responses={401: _problem("auth.invalid_token")},
)
async def list_products(
    _principal: CurrentPrincipal,
    session: DbSession,
    lang: Lang,
    response: Response,
    kind: Kind | None = None,
    category: Annotated[str | None, Query(max_length=60, description="Category slug")] = None,
    tag: Annotated[str | None, Query(max_length=40)] = None,
    cursor: Cursor = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> Page[ProductOut]:
    rows, next_cursor = await service.list_products(
        session, kind=kind, category=category, tag=tag, cursor=cursor, limit=limit
    )
    response.headers["Cache-Control"] = CATALOG_CACHE
    return Page(items=[_product_out(row, lang) for row in rows], next_cursor=next_cursor)


@router.get(
    "/products/{product_id}",
    summary="One product",
    responses={401: _problem("auth.invalid_token"), 404: _problem("catalog.product_not_found")},
)
async def get_product(
    product_id: uuid.UUID,
    _principal: CurrentPrincipal,
    session: DbSession,
    lang: Lang,
    response: Response,
) -> ProductOut:
    row = await service.get(session, product_id)
    response.headers["Cache-Control"] = CATALOG_CACHE
    return _product_out(row, lang)


@router.get(
    "/search",
    summary="Find products by any spelling: English, Hindi, Marathi or Hinglish",
    responses={401: _problem("auth.invalid_token")},
)
async def search(
    _principal: CurrentPrincipal,
    session: DbSession,
    lang: Lang,
    response: Response,
    q: Annotated[str, Query(min_length=1, max_length=80, examples=["bhindi"])],
    limit: Annotated[int, Query(ge=1, le=20)] = 10,
) -> SearchResults:
    term = " ".join(q.split())
    hits = await service.search(session, term, limit=limit) if term else []
    response.headers["Cache-Control"] = "no-store"
    return SearchResults(
        items=[
            SearchItem(
                product_id=hit.product.id,
                slug=hit.product.slug,
                name=localised(hit.product.names, lang),
                matched=hit.matched,
                step=_step(hit.product.step_qty, hit.product.step_unit, lang),
                price_paise=None,
            )
            for hit in hits
        ]
    )
