"""HTTP endpoints of the console's festival calendar and price lists (spec §3.3).

Festivals are edited by admins (`catalog:write`). Price lists by a hub's manager or an admin
(`prices:write`): a list that took effect before today is locked; today's or a future list can be
replaced (If-Match), and a future one deleted.
"""

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from gharbasket.modules.catalog import festival_admin, pricing
from gharbasket.modules.catalog import permissions as _permissions  # noqa: F401 (registers rules)
from gharbasket.modules.catalog.admin_router import CanWrite, problem
from gharbasket.modules.catalog.models import Festival, PriceList
from gharbasket.modules.catalog.schemas import (
    AdminFestival,
    AdminFestivals,
    FestivalIn,
    FestivalPatch,
    PriceListDetail,
    PriceListItemsIn,
    PriceLists,
    PriceListSummary,
)
from gharbasket.modules.catalog.service import step_label
from gharbasket.modules.identity import api as identity_api
from gharbasket.platform.db import DbSession
from gharbasket.platform.etag import IfMatch, set_etag
from gharbasket.platform.idempotency import Idempotent
from gharbasket.platform.locale import Lang, localised
from gharbasket.platform.permissions import Permit, require

router = APIRouter(tags=["catalog"])

FESTIVALS = "/admin/festivals"
FESTIVAL = FESTIVALS + "/{day}/{code}/{region}"
LISTS = "/hubs/{hub_id}/price-lists"
CanPrice = Annotated[Permit, require("prices:write")]


async def _festivals(session: DbSession, found: list[Festival]) -> list[AdminFestival]:
    who = await identity_api.user_names(session, {f.updated_by for f in found if f.updated_by})
    return [
        AdminFestival.model_validate(
            {
                **f.as_dict(),
                "date": f.day,
                "updated_by": f.updated_by and {"id": f.updated_by, "name": who.get(f.updated_by)},
            }
        )
        for f in found
    ]


@router.get(
    FESTIVALS,
    summary="Festival days for the console's calendar, by date (at most 366 days)",
    responses={403: problem("auth.forbidden"), 422: problem("request.invalid")},
)
async def list_admin_festivals(
    _permit: CanWrite,
    session: DbSession,
    start: Annotated[date | None, Query(alias="from", description="Default: today (IST)")] = None,
    end: Annotated[date | None, Query(alias="to", description="Default: from + 30 days")] = None,
    region: Annotated[str | None, Query(max_length=40, description="Only this region")] = None,
) -> AdminFestivals:
    found = await festival_admin.list_days(session, start, end, region)
    return AdminFestivals(items=await _festivals(session, found))


@router.post(
    FESTIVALS,
    status_code=status.HTTP_201_CREATED,
    summary="Add a festival day (admin; Idempotency-Key; source manual; audit-logged)",
    responses={
        403: problem("auth.forbidden"),
        409: problem("catalog.festival_exists"),
        422: problem("catalog.names_incomplete (missing), request.invalid"),
    },
)
async def create_festival(
    body: FestivalIn, permit: CanWrite, session: DbSession, idem: Idempotent
) -> AdminFestival:
    festival = await festival_admin.create(session, body, permit.principal.user_id)
    [out] = await _festivals(session, [festival])
    await idem.save(201, out)
    await session.commit()
    return out


@router.patch(
    FESTIVAL,
    summary="Edit a festival day's names, rules or verified flag (admin; last write wins)",
    responses={
        403: problem("auth.forbidden"),
        404: problem("catalog.festival_not_found"),
        422: problem("catalog.names_incomplete (missing), request.invalid"),
    },
)
async def edit_festival(
    day: date, code: str, region: str, body: FestivalPatch, permit: CanWrite, session: DbSession
) -> AdminFestival:
    festival = await festival_admin.update(
        session, day, code, region, body, permit.principal.user_id
    )
    [out] = await _festivals(session, [festival])
    await session.commit()
    return out


@router.delete(
    FESTIVAL,
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a festival day after today (admin; audit-logged)",
    responses={
        403: problem("auth.forbidden"),
        404: problem("catalog.festival_not_found"),
        409: problem("catalog.festival_past"),
    },
)
async def delete_festival(
    day: date, code: str, region: str, permit: CanWrite, session: DbSession
) -> None:
    await festival_admin.delete(session, day, code, region, permit.principal.user_id)
    await session.commit()


async def _summaries(
    session: DbSession, found: list[tuple[PriceList, int]]
) -> list[PriceListSummary]:
    who = await identity_api.user_names(
        session, {p.published_by for p, _ in found if p.published_by}
    )
    today = pricing.today()
    return [
        PriceListSummary.model_validate(
            {
                **p.as_dict(),
                "items_count": count,
                "published_by": p.published_by
                and {"id": p.published_by, "name": who.get(p.published_by)},
                "editable": p.valid_from >= today,
            }
        )
        for p, count in found
    ]


async def _detail(
    session: DbSession, hub_id: uuid.UUID, list_id: uuid.UUID, lang: Lang, response: Response
) -> PriceListDetail:
    found, lines = await pricing.get_list(session, hub_id, list_id)
    [summary] = await _summaries(session, [(found, len(lines))])
    set_etag(response, found.version)
    response.headers["Cache-Control"] = "no-store"
    return PriceListDetail.model_validate(
        {
            **summary.model_dump(),
            "items": [
                {
                    "product_id": price.product_id,
                    "slug": product.slug,
                    "name": localised(product.names, lang),
                    "step": {
                        "qty": product.step_qty,
                        "unit": product.step_unit,
                        "label": step_label(product.step_qty, product.step_unit, lang),
                    },
                    "price_paise": price.price_paise,
                    "mrp_paise": price.mrp_paise,
                }
                for price, product in lines
            ],
        }
    )


@router.get(
    LISTS,
    summary="A hub's price lists, newest first (default: the last 30 days and every future one)",
    responses={403: problem("auth.forbidden: not this hub's manager")},
)
async def list_price_lists(
    hub_id: uuid.UUID,
    permit: CanPrice,
    session: DbSession,
    response: Response,
    start: Annotated[date | None, Query(alias="from", description="Default: 30 days ago")] = None,
    end: Annotated[date | None, Query(alias="to", description="Default: no limit")] = None,
) -> PriceLists:
    permit.ensure(hub_id)
    response.headers["Cache-Control"] = "no-store"
    found = await pricing.list_lists(session, hub_id, start, end)
    return PriceLists(items=await _summaries(session, found))


@router.get(
    LISTS + "/{list_id}",
    summary="One price list with its items (ETag: its version)",
    responses={
        403: problem("auth.forbidden: not this hub's manager"),
        404: problem("catalog.price_list_not_found"),
    },
)
async def get_price_list(
    hub_id: uuid.UUID,
    list_id: uuid.UUID,
    permit: CanPrice,
    session: DbSession,
    lang: Lang,
    response: Response,
) -> PriceListDetail:
    permit.ensure(hub_id)
    return await _detail(session, hub_id, list_id, lang, response)


@router.put(
    LISTS + "/{list_id}",
    summary="Replace every item of today's or a future price list (If-Match; audit-logged)",
    responses={
        403: problem("auth.forbidden: not this hub's manager"),
        404: problem("catalog.price_list_not_found"),
        409: problem("catalog.price_list_locked"),
        412: problem("catalog.version_conflict"),
        422: problem(
            "catalog.price_above_mrp, catalog.product_unknown (with product_ids), request.invalid"
        ),
        428: problem("request.precondition_required"),
    },
)
async def replace_price_list(
    hub_id: uuid.UUID,
    list_id: uuid.UUID,
    body: PriceListItemsIn,
    permit: CanPrice,
    session: DbSession,
    expected: IfMatch,
    lang: Lang,
    response: Response,
) -> PriceListDetail:
    permit.ensure(hub_id)
    await pricing.replace_list(
        session, hub_id, list_id, expected, body.items, permit.principal.user_id
    )
    out = await _detail(session, hub_id, list_id, lang, response)
    await session.commit()
    return out


@router.delete(
    LISTS + "/{list_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a price list that starts after today (audit-logged)",
    responses={
        403: problem("auth.forbidden: not this hub's manager"),
        404: problem("catalog.price_list_not_found"),
        409: problem("catalog.price_list_locked"),
    },
)
async def delete_price_list(
    hub_id: uuid.UUID, list_id: uuid.UUID, permit: CanPrice, session: DbSession
) -> None:
    permit.ensure(hub_id)
    await pricing.delete_list(session, hub_id, list_id, permit.principal.user_id)
    await session.commit()
