"""HTTP endpoints of M2 Catalog & Pricing: products, search, prices, substitutes (spec §3.1),
recipes and festivals (§3.2).

Prices are for the caller's hub: `hub_id` if given, else the hub of their household (M1), else none.
"""

import uuid
from datetime import date, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Query, Response, status

from gharbasket.modules.catalog import permissions as _permissions  # noqa: F401 (registers rules)
from gharbasket.modules.catalog import pricing, recipes, service
from gharbasket.modules.catalog.pricing import HubPrice
from gharbasket.modules.catalog.schemas import (
    CategoryRef,
    Cuisine,
    FestivalOut,
    FestivalRules,
    Festivals,
    IngredientOut,
    Kind,
    Names,
    Price,
    PriceItem,
    PriceListCreated,
    PriceListIn,
    PriceListOut,
    ProductOut,
    RecipeOut,
    RecipeRules,
    RecipeSummary,
    SearchItem,
    SearchResults,
    Slot,
    Step,
    SubstituteOut,
    Substitutes,
)
from gharbasket.modules.catalog.substitutes import substitutes as rank_substitutes
from gharbasket.modules.identity import api as identity_api
from gharbasket.platform.auth import CurrentPrincipal
from gharbasket.platform.db import DbSession
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.idempotency import Idempotent
from gharbasket.platform.locale import Lang, Language, localised
from gharbasket.platform.pagination import Cursor, Page
from gharbasket.platform.permissions import Permit, require
from gharbasket.platform.tokens import AccessClaims

router = APIRouter(tags=["catalog"])

# Products change rarely; the PWA's service worker caches them per language (spec §3.1).
CATALOG_CACHE = "private, max-age=300"


def _problem(description: str) -> dict[str, Any]:
    return {"description": description, "content": {"application/problem+json": {}}}


HubParam = Annotated[
    uuid.UUID | None,
    Query(description="Price at this hub; by default the hub of the caller's household"),
]


async def _hub(
    session: DbSession, principal: AccessClaims, hub_id: uuid.UUID | None
) -> uuid.UUID | None:
    return hub_id or await identity_api.household_hub(session, principal.user_id)


def _price(price: HubPrice | None) -> Price | None:
    if price is None:
        return None
    return Price(
        price_paise=price.price_paise,
        mrp_paise=price.mrp_paise,
        hub_id=price.hub_id,
        valid_from=price.valid_from,
    )


def _step(qty: int, unit: str, lang: Language) -> Step:
    return Step(qty=qty, unit=unit, label=service.step_label(qty, unit, lang))  # pyright: ignore[reportArgumentType]


def _product_out(row: service.ProductRow, lang: Language, price: HubPrice | None) -> ProductOut:
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
        price=_price(price),
    )


@router.get(
    "/products",
    summary="Products, alphabetical (names in the Accept-Language language)",
    responses={401: _problem("auth.invalid_token")},
)
async def list_products(
    principal: CurrentPrincipal,
    session: DbSession,
    lang: Lang,
    response: Response,
    kind: Kind | None = None,
    category: Annotated[str | None, Query(max_length=60, description="Category slug")] = None,
    tag: Annotated[str | None, Query(max_length=40)] = None,
    hub_id: HubParam = None,
    cursor: Cursor = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> Page[ProductOut]:
    rows, next_cursor = await service.list_products(
        session, kind=kind, category=category, tag=tag, cursor=cursor, limit=limit
    )
    hub = await _hub(session, principal, hub_id)
    prices = await pricing.prices_for(session, hub, [row.product.id for row in rows])
    response.headers["Cache-Control"] = CATALOG_CACHE
    return Page(
        items=[_product_out(row, lang, prices.get(row.product.id)) for row in rows],
        next_cursor=next_cursor,
    )


@router.get(
    "/products/{product_id}",
    summary="One product",
    responses={401: _problem("auth.invalid_token"), 404: _problem("catalog.product_not_found")},
)
async def get_product(
    product_id: uuid.UUID,
    principal: CurrentPrincipal,
    session: DbSession,
    lang: Lang,
    response: Response,
    hub_id: HubParam = None,
) -> ProductOut:
    row = await service.get(session, product_id)
    hub = await _hub(session, principal, hub_id)
    prices = await pricing.prices_for(session, hub, [product_id])
    response.headers["Cache-Control"] = CATALOG_CACHE
    return _product_out(row, lang, prices.get(product_id))


@router.get(
    "/search",
    summary="Find products by any spelling: English, Hindi, Marathi or Hinglish",
    responses={401: _problem("auth.invalid_token")},
)
async def search(
    principal: CurrentPrincipal,
    session: DbSession,
    lang: Lang,
    response: Response,
    q: Annotated[str, Query(min_length=1, max_length=80, examples=["bhindi"])],
    hub_id: HubParam = None,
    limit: Annotated[int, Query(ge=1, le=20)] = 10,
) -> SearchResults:
    term = " ".join(q.split())
    hits = await service.search(session, term, limit=limit) if term else []
    hub = await _hub(session, principal, hub_id)
    prices = await pricing.prices_for(session, hub, [hit.product.id for hit in hits])
    response.headers["Cache-Control"] = "no-store"
    return SearchResults(
        items=[
            SearchItem(
                product_id=hit.product.id,
                slug=hit.product.slug,
                name=localised(hit.product.names, lang),
                matched=hit.matched,
                step=_step(hit.product.step_qty, hit.product.step_unit, lang),
                price_paise=price.price_paise if (price := prices.get(hit.product.id)) else None,
            )
            for hit in hits
        ]
    )


@router.get(
    "/products/{product_id}/substitutes",
    summary="Ranked substitutes, with the price difference and why",
    responses={401: _problem("auth.invalid_token"), 404: _problem("catalog.product_not_found")},
)
async def substitutes(
    product_id: uuid.UUID,
    principal: CurrentPrincipal,
    session: DbSession,
    lang: Lang,
    hub_id: HubParam = None,
    limit: Annotated[int, Query(ge=1, le=10)] = 3,
) -> Substitutes:
    hub = await _hub(session, principal, hub_id)
    ranked = await rank_substitutes(session, product_id, hub, limit)
    return Substitutes(
        items=[
            SubstituteOut(
                product_id=c.product.id,
                slug=c.product.slug,
                name=localised(c.product.names, lang),
                step=_step(c.product.step_qty, c.product.step_unit, lang),
                price_paise=c.price.price_paise if c.price else None,
                price_delta_paise=c.price_delta_paise,
                score=c.score,
                reason_code=c.reason_code,  # pyright: ignore[reportArgumentType]
                reason_params=c.reason_params,
            )
            for c in ranked
        ]
    )


@router.get(
    "/prices",
    summary="A hub's current price list (prices per step, in paise)",
    responses={
        401: _problem("auth.invalid_token"),
        404: _problem("catalog.price_list_not_found"),
    },
)
async def get_prices(
    _principal: CurrentPrincipal,
    session: DbSession,
    response: Response,
    hub_id: uuid.UUID,
    on: Annotated[date | None, Query(alias="date", description="Default: today (IST)")] = None,
) -> PriceListOut:
    found, prices = await pricing.price_list(session, hub_id, on or pricing.today())
    response.headers["Cache-Control"] = "no-store"
    return PriceListOut(
        id=found.id,
        hub_id=found.hub_id,
        valid_from=found.valid_from,
        items=[
            PriceItem(product_id=p.product_id, price_paise=p.price_paise, mrp_paise=p.mrp_paise)
            for p in prices
        ],
    )


@router.post(
    "/hubs/{hub_id}/price-lists",
    status_code=status.HTTP_201_CREATED,
    summary="Publish a hub's prices from a date (hub manager of that hub; audit-logged)",
    responses={
        403: _problem("auth.forbidden: not this hub's manager"),
        409: _problem("catalog.price_list_exists"),
        422: _problem(
            "catalog.valid_from_past, catalog.price_above_mrp, catalog.product_unknown "
            "(with product_ids), request.invalid"
        ),
    },
)
async def publish_price_list(
    hub_id: uuid.UUID,
    body: PriceListIn,
    permit: Annotated[Permit, require("prices:write")],
    session: DbSession,
    idem: Idempotent,
) -> PriceListCreated:
    permit.ensure(hub_id)
    created = await pricing.publish_price_list(session, hub_id, body, permit.principal.user_id)
    out = PriceListCreated(
        id=created.id,
        hub_id=created.hub_id,
        valid_from=created.valid_from,
        items_count=len(body.items),
    )
    await idem.save(201, out)
    await session.commit()
    return out


# --- recipes and festivals (spec §3.2) ---------------------------------------------------------

FESTIVAL_RANGE_DAYS = 92


def _recipe_summary(recipe: Any, lang: Language) -> RecipeSummary:
    return RecipeSummary(
        id=recipe.id,
        slug=recipe.slug,
        name=localised(recipe.names, lang),
        names=Names.model_validate(recipe.names),
        cuisine=recipe.cuisine,
        slots=recipe.slots,
        cook_minutes=recipe.cook_minutes,
        servings=recipe.servings,
        diet=recipe.diet,
        rules=RecipeRules.model_validate(recipe.rules),
        tags=recipe.tags,
        is_side=recipe.is_side,
        sides=recipe.sides,
    )


@router.get(
    "/recipes",
    summary="Recipes the planner can choose, alphabetical",
    responses={401: _problem("auth.invalid_token")},
)
async def list_recipes(
    _principal: CurrentPrincipal,
    session: DbSession,
    lang: Lang,
    response: Response,
    cuisine: Cuisine | None = None,
    slot: Slot | None = None,
    diet: Annotated[str | None, Query(pattern="^(veg|egg)$")] = None,
    jain_ok: bool | None = None,
    fasting_ok: bool | None = None,
    is_side: Annotated[
        bool | None, Query(description="false: only dishes planned on their own")
    ] = None,
    cursor: Cursor = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> Page[RecipeSummary]:
    found, next_cursor = await recipes.list_recipes(
        session,
        cuisine=cuisine,
        slot=slot,
        diet=diet,
        jain_ok=jain_ok,
        fasting_ok=fasting_ok,
        cursor=cursor,
        limit=limit,
        is_side=is_side,
    )
    response.headers["Cache-Control"] = CATALOG_CACHE
    return Page(items=[_recipe_summary(recipe, lang) for recipe in found], next_cursor=next_cursor)


@router.get(
    "/recipes/{recipe_id}",
    summary="One recipe with its ingredients and the cost of a serving at the caller's hub",
    responses={401: _problem("auth.invalid_token"), 404: _problem("catalog.recipe_not_found")},
)
async def get_recipe(
    recipe_id: uuid.UUID,
    principal: CurrentPrincipal,
    session: DbSession,
    lang: Lang,
    hub_id: HubParam = None,
) -> RecipeOut:
    recipe, ingredients = await recipes.get_recipe(session, recipe_id)
    cost = await recipes.cost_per_serving(
        session, ingredients, await _hub(session, principal, hub_id)
    )
    return RecipeOut(
        **_recipe_summary(recipe, lang).model_dump(),
        ingredients=[
            IngredientOut(
                product_id=i.product.id,
                slug=i.product.slug,
                name=localised(i.product.names, lang),
                qty=float(i.qty),
                unit=i.unit,  # pyright: ignore[reportArgumentType]
                label=service.step_label(_number(i.qty), i.unit, lang),
                optional=i.optional,
            )
            for i in ingredients
        ],
        cost_per_serving_paise=cost,
    )


def _number(qty: Any) -> Any:
    """50.00 → 50, 0.50 → 0.5 (for labels)."""
    return int(qty) if qty == int(qty) else float(qty)


@router.get(
    "/festivals",
    summary="Festival and fasting days in a date range (at most 92 days)",
    responses={401: _problem("auth.invalid_token"), 422: _problem("request.invalid")},
)
async def list_festivals(
    _principal: CurrentPrincipal,
    session: DbSession,
    lang: Lang,
    start: Annotated[date | None, Query(alias="from", description="Default: today (IST)")] = None,
    end: Annotated[date | None, Query(alias="to", description="Default: from + 30 days")] = None,
    region: Annotated[str | None, Query(max_length=40, examples=["maharashtra"])] = None,
) -> Festivals:
    first = start or pricing.today()
    last = end or first + timedelta(days=30)
    if last < first or (last - first).days > FESTIVAL_RANGE_DAYS:
        raise ProblemError(
            422, "request.invalid", f"Use a range of 0 to {FESTIVAL_RANGE_DAYS} days."
        )
    found = await recipes.festivals(session, first, last, region)
    return Festivals(
        items=[
            FestivalOut(
                date=festival.day,
                code=festival.code,
                name=localised(festival.names, lang),
                region=festival.region,
                rules=FestivalRules.model_validate(festival.rules),
                verified=festival.verified,
            )
            for festival in found
        ]
    )
