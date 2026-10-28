"""HTTP endpoints of the console's tiffin menu review (spec §3.4).

The list is read by a hub's manager (own hub), catalog editors and admins (`tiffin_menus:read`);
approve, send back and reopen are `tiffin_menus:review`, held by the same three roles. A hub
manager reviews only the menus of their hubs; a catalog editor or admin any hub's. No money or
order moves, so there is no Idempotency-Key: a repeated approve is a 200, the other repeats 409.
"""

import uuid
from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Query, Response

from gharbasket.modules.catalog import permissions as _permissions  # noqa: F401 (registers rules)
from gharbasket.modules.catalog import tiffin_review
from gharbasket.modules.catalog.admin_router import problem
from gharbasket.modules.catalog.pricing import today
from gharbasket.modules.catalog.schemas import MenuStatus, SendBackIn, TiffinMenuReview
from gharbasket.platform.db import DbSession
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.locale import Lang
from gharbasket.platform.pagination import Cursor, Page
from gharbasket.platform.permissions import Permit, forbidden, require

router = APIRouter(tags=["catalog"])

CanReadMenus = Annotated[Permit, require("tiffin_menus:read")]
CanReview = Annotated[Permit, require("tiffin_menus:review")]
HUB_MENUS = "/hubs/{hub_id}/tiffin-menus"
MENU = "/tiffin-menus/{menu_id}"
EDITORS = "catalog_editor"


def _ensure_hub(permit: Permit, hub_id: uuid.UUID) -> None:
    """Admins and catalog editors cover every hub; a hub manager only their own."""
    if not (permit.has_role(EDITORS) or permit.allows(hub_id)):
        raise forbidden()


@router.get(
    HUB_MENUS,
    summary=(
        "A hub's tiffin menus of every status for review: waiting ones first, oldest first, "
        "then by date and meal (default: today to 7 days on)"
    ),
    responses={403: problem("auth.forbidden"), 422: problem("request.invalid")},
)
async def list_hub_menus(
    hub_id: uuid.UUID,
    permit: CanReadMenus,
    session: DbSession,
    lang: Lang,
    response: Response,
    status: MenuStatus | None = None,
    start: Annotated[date | None, Query(alias="from", description="Default: today (IST)")] = None,
    end: Annotated[date | None, Query(alias="to", description="Default: today + 7 days")] = None,
    cursor: Cursor = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> Page[TiffinMenuReview]:
    _ensure_hub(permit, hub_id)
    first = start or today()
    last = end or first + timedelta(days=tiffin_review.DEFAULT_DAYS)
    if last < first:
        raise ProblemError(422, "request.invalid", "`to` is before `from`.")
    found, next_cursor = await tiffin_review.list_menus(
        session, hub_id, status=status, first=first, last=last, cursor=cursor, limit=limit
    )
    response.headers["Cache-Control"] = "no-store"
    return Page(items=await tiffin_review.reviews(session, found, lang), next_cursor=next_cursor)


async def _guarded(session: DbSession, permit: Permit, menu_id: uuid.UUID) -> None:
    """404 for an unknown menu, 403 for another hub's, before anything is changed."""
    menu = await tiffin_review.get(session, menu_id)
    _ensure_hub(permit, menu.hub_id)


async def _answer(
    session: DbSession, menu_id: uuid.UUID, lang: Lang, response: Response
) -> TiffinMenuReview:
    menu = await tiffin_review.get(session, menu_id)
    [out] = await tiffin_review.reviews(session, [menu], lang)
    await session.commit()
    response.headers["Cache-Control"] = "no-store"
    return out


@router.post(
    MENU + "/approve",
    summary="Approve a submitted menu so customers can book it (an approved menu: 200, unchanged)",
    responses={
        403: problem("auth.forbidden"),
        404: problem("catalog.menu_not_found"),
        409: problem("catalog.menu_not_submitted, catalog.menu_closed (past order_by)"),
        422: problem(
            "catalog.menu_invalid (reasons: product_inactive, recipe_inactive, price_invalid)"
        ),
    },
)
async def approve_menu(
    menu_id: uuid.UUID, permit: CanReview, session: DbSession, lang: Lang, response: Response
) -> TiffinMenuReview:
    await _guarded(session, permit, menu_id)
    await tiffin_review.approve(session, menu_id, permit.principal.user_id)
    return await _answer(session, menu_id, lang, response)


@router.post(
    MENU + "/send-back",
    summary="Send a submitted menu back to its kitchen with a reason",
    responses={
        403: problem("auth.forbidden"),
        404: problem("catalog.menu_not_found"),
        409: problem("catalog.menu_not_submitted"),
        422: problem("request.invalid (no note)"),
    },
)
async def send_back_menu(
    menu_id: uuid.UUID,
    body: SendBackIn,
    permit: CanReview,
    session: DbSession,
    lang: Lang,
    response: Response,
) -> TiffinMenuReview:
    await _guarded(session, permit, menu_id)
    await tiffin_review.send_back(session, menu_id, permit.principal.user_id, body.note)
    return await _answer(session, menu_id, lang, response)


@router.post(
    MENU + "/reopen",
    summary="Take an approved or sent-back menu back to review (the console's undo)",
    responses={
        403: problem("auth.forbidden"),
        404: problem("catalog.menu_not_found"),
        409: problem("catalog.menu_not_decided, catalog.menu_closed (past order_by)"),
    },
)
async def reopen_menu(
    menu_id: uuid.UUID, permit: CanReview, session: DbSession, lang: Lang, response: Response
) -> TiffinMenuReview:
    await _guarded(session, permit, menu_id)
    await tiffin_review.reopen(session, menu_id, permit.principal.user_id)
    return await _answer(session, menu_id, lang, response)
