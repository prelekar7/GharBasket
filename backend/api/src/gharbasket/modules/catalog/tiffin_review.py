"""The review of kitchens' tiffin menus (spec §3.4): the hub's list of every status, and approve,
send back and reopen.

Only approved menus are bookable (M4 §3.5). A decision is audit-logged and published as
`catalog.tiffin_menu_decided`. A menu closes for review at its cut-off (21:00 IST the day before
its date), because customers may already have booked it; orders already booked stand.
"""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import case, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog import events, recipe_admin
from gharbasket.modules.catalog.models import Product, Recipe, TiffinMenu
from gharbasket.modules.catalog.schemas import TiffinMenuReview
from gharbasket.modules.identity import api as identity_api
from gharbasket.modules.partners import api as partners_api
from gharbasket.platform import audit
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.locale import Language, localised
from gharbasket.platform.outbox import publish
from gharbasket.platform.pagination import decode_cursor, encode_cursor

IST = ZoneInfo("Asia/Kolkata")
CUTOFF_TIME = time(21, 0)
DEFAULT_DAYS = 7
RULE_CODES = ("jain_ok", "no_onion_garlic", "fasting_ok", "diabetic_ok")
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def now() -> datetime:
    return datetime.now(UTC)


def order_by(on_date: date) -> datetime:
    """The menu's cut-off: 21:00 IST on the day before its date, as a UTC instant."""
    return datetime.combine(on_date - timedelta(days=1), CUTOFF_TIME, IST).astimezone(UTC)


def not_found() -> ProblemError:
    return ProblemError(404, "catalog.menu_not_found", "No such tiffin menu.")


def _invalid_cursor() -> ProblemError:
    return ProblemError(422, "request.invalid_cursor", "The cursor is not valid.")


# --- reading ---------------------------------------------------------------------------------


async def list_menus(
    session: AsyncSession,
    hub_id: uuid.UUID,
    *,
    status: str | None,
    first: date,
    last: date,
    cursor: str | None,
    limit: int,
) -> tuple[list[TiffinMenu], str | None]:
    """The hub's menus in the date range: waiting ones first (oldest submission first), then by
    date and meal."""
    waiting = TiffinMenu.status == "submitted"
    rank = case((waiting, 0), else_=1)
    submitted = case((waiting, TiffinMenu.submitted_at), else_=_EPOCH)
    stmt = select(TiffinMenu).where(
        TiffinMenu.hub_id == hub_id, TiffinMenu.on_date.between(first, last)
    )
    if status is not None:
        stmt = stmt.where(TiffinMenu.status == status)
    if cursor is not None:
        after = decode_cursor(cursor).get("after")
        try:
            assert isinstance(after, list) and len(after) == 5  # pyright: ignore[reportUnknownArgumentType]
            position = (
                int(after[0]),  # pyright: ignore[reportUnknownArgumentType]
                datetime.fromisoformat(str(after[1])),  # pyright: ignore[reportUnknownArgumentType]
                date.fromisoformat(str(after[2])),  # pyright: ignore[reportUnknownArgumentType]
                str(after[3]),  # pyright: ignore[reportUnknownArgumentType]
                uuid.UUID(str(after[4])),  # pyright: ignore[reportUnknownArgumentType]
            )
        except (AssertionError, ValueError, TypeError):
            raise _invalid_cursor() from None
        stmt = stmt.where(
            tuple_(rank, submitted, TiffinMenu.on_date, TiffinMenu.meal, TiffinMenu.id)
            > tuple_(*position)
        )
    stmt = stmt.order_by(rank, submitted, TiffinMenu.on_date, TiffinMenu.meal, TiffinMenu.id)
    rows = list((await session.execute(stmt.limit(limit + 1))).scalars())
    found = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        last_row = found[-1]
        is_waiting = last_row.status == "submitted"
        next_cursor = encode_cursor(
            {
                "after": [
                    0 if is_waiting else 1,
                    (last_row.submitted_at if is_waiting else _EPOCH).isoformat(),
                    last_row.on_date.isoformat(),
                    last_row.meal,
                    str(last_row.id),
                ]
            }
        )
    return found, next_cursor


async def get(session: AsyncSession, menu_id: uuid.UUID, *, lock: bool = False) -> TiffinMenu:
    stmt = select(TiffinMenu).where(TiffinMenu.id == menu_id)
    menu = (await session.execute(stmt.with_for_update() if lock else stmt)).scalar_one_or_none()
    if menu is None:
        raise not_found()
    return menu


async def reviews(
    session: AsyncSession, menus: list[TiffinMenu], lang: Language
) -> list[TiffinMenuReview]:
    """The review shape of each menu: kitchen, product, dishes, derived rules and ingredients."""
    if not menus:
        return []
    recipe_ids = {rid for menu in menus for rid in menu.recipe_ids}
    recipes: dict[uuid.UUID, Recipe] = {}
    if recipe_ids:
        found = await session.execute(select(Recipe).where(Recipe.id.in_(recipe_ids)))
        recipes = {r.id: r for r in found.scalars()}
    lines = await recipe_admin.ingredients(session, list(recipes))
    products = {
        p.id: p
        for p in (
            await session.execute(
                select(Product).where(Product.id.in_({m.product_id for m in menus}))
            )
        ).scalars()
    }
    kitchens: dict[uuid.UUID, str | None] = {}
    for partner_id in {m.partner_id for m in menus}:
        card = await partners_api.profile_card(session, partner_id)
        kitchens[partner_id] = card.display_name if card else None
    reviewers = await identity_api.user_names(
        session, {m.decided_by for m in menus if m.decided_by}
    )

    out: list[TiffinMenuReview] = []
    for menu in menus:
        dishes = [recipes[rid] for rid in menu.recipe_ids if rid in recipes]
        contains: list[str] = []
        for dish in dishes:
            for line in lines[dish.id]:
                name = localised(line.product.names, lang)
                if not line.optional and name not in contains:
                    contains.append(name)
        product = products[menu.product_id]
        out.append(
            TiffinMenuReview.model_validate(
                {
                    "id": menu.id,
                    "hub_id": menu.hub_id,
                    "date": menu.on_date,
                    "meal": menu.meal,
                    "status": menu.status,
                    "kitchen": {
                        "partner_id": menu.partner_id,
                        "display_name": kitchens[menu.partner_id],
                    },
                    "product": {
                        "id": product.id,
                        "slug": product.slug,
                        "name": localised(product.names, lang),
                        "tags": list(product.tags),
                    },
                    "dishes": [
                        {"recipe_id": d.id, "name": localised(d.names, lang)} for d in dishes
                    ],
                    "price_paise": menu.price_paise,
                    "rules": {
                        code: bool(dishes) and all(d.rules.get(code, False) for d in dishes)
                        for code in RULE_CODES
                    },
                    "contains": contains,
                    "order_by": order_by(menu.on_date),
                    "submitted_at": menu.submitted_at,
                    "decided_at": menu.decided_at,
                    "decided_by": menu.decided_by
                    and {"id": menu.decided_by, "name": reviewers.get(menu.decided_by)},
                    "note": menu.decision_note,
                    "version": menu.version,
                }
            )
        )
    return out


# --- deciding --------------------------------------------------------------------------------


async def _problems(session: AsyncSession, menu: TiffinMenu) -> list[str]:
    """Why a menu cannot be approved: its product or a dish is inactive, or its price is not > 0."""
    reasons: list[str] = []
    product = await session.get(Product, menu.product_id)
    if product is None or not product.active:
        reasons.append("product_inactive")
    if menu.recipe_ids:
        active = set(
            (
                await session.execute(
                    select(Recipe.id).where(Recipe.id.in_(menu.recipe_ids), Recipe.active)
                )
            ).scalars()
        )
        if any(rid not in active for rid in menu.recipe_ids):
            reasons.append("recipe_inactive")
    if menu.price_paise <= 0:
        reasons.append("price_invalid")
    return reasons


def _closed(menu: TiffinMenu) -> ProblemError:
    return ProblemError(
        409, "catalog.menu_closed", "This menu is past its cut-off; customers may have booked it."
    )


async def _decide(
    session: AsyncSession,
    menu: TiffinMenu,
    actor: uuid.UUID,
    *,
    status: str,
    note: str | None,
    decided: bool,
) -> None:
    """Move the menu to `status` (version + 1), audit it and publish the decision."""
    before = {"status": menu.status, "note": menu.decision_note}
    menu.status = status
    menu.decision_note = note
    menu.decided_at = now() if decided else None
    menu.decided_by = actor if decided else None
    menu.version += 1
    await session.flush()
    await audit.record(
        session,
        actor_user_id=actor,
        action="catalog.tiffin_menu_decided",
        entity_type="tiffin_menu",
        entity_id=str(menu.id),
        before=before,
        after={"status": status, "note": note},
    )
    payload: dict[str, Any] = {
        "menu_id": str(menu.id),
        "partner_id": str(menu.partner_id),
        "hub_id": str(menu.hub_id),
        "date": menu.on_date.isoformat(),
        "meal": menu.meal,
        "status": status,
    }
    await publish(session, events.TIFFIN_MENU_DECIDED, payload)


async def approve(session: AsyncSession, menu_id: uuid.UUID, actor: uuid.UUID) -> TiffinMenu:
    """Approve a submitted menu; an approved one stays as it is (a repeat is a 200)."""
    menu = await get(session, menu_id, lock=True)
    if menu.status == "approved":
        return menu
    if menu.status != "submitted":
        raise ProblemError(
            409, "catalog.menu_not_submitted", "This menu is not waiting for review."
        )
    if now() >= order_by(menu.on_date):
        raise _closed(menu)
    if reasons := await _problems(session, menu):
        raise ProblemError(
            422,
            "catalog.menu_invalid",
            "The menu cannot be approved as it is.",
            extra={"reasons": reasons},
        )
    await _decide(session, menu, actor, status="approved", note=None, decided=True)
    return menu


async def send_back(
    session: AsyncSession, menu_id: uuid.UUID, actor: uuid.UUID, note: str
) -> TiffinMenu:
    menu = await get(session, menu_id, lock=True)
    if menu.status != "submitted":
        raise ProblemError(
            409, "catalog.menu_not_submitted", "This menu is not waiting for review."
        )
    await _decide(session, menu, actor, status="sent_back", note=note, decided=True)
    return menu


async def reopen(session: AsyncSession, menu_id: uuid.UUID, actor: uuid.UUID) -> TiffinMenu:
    """Undo a decision: an approved or sent-back menu goes back to review, and an approved one
    leaves the customers' list at once (orders already booked stand)."""
    menu = await get(session, menu_id, lock=True)
    if menu.status not in ("approved", "sent_back"):
        raise ProblemError(409, "catalog.menu_not_decided", "This menu has not been decided.")
    if now() >= order_by(menu.on_date):
        raise _closed(menu)
    await _decide(session, menu, actor, status="submitted", note=None, decided=False)
    return menu
