"""Hub price lists (spec §3.1, US-3): prices per step, in paise, from a `valid_from` date.

The current list of a hub on a date is the newest one with `valid_from` on or before it. A list
that took effect before today is locked, so past orders keep the prices they were made with; the
console replaces today's or a future list and deletes a future one (§3.3).
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog import events
from gharbasket.modules.catalog.models import Price, PriceList, Product
from gharbasket.modules.catalog.schemas import PriceItem, PriceListIn
from gharbasket.platform import audit
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.outbox import publish

IST = ZoneInfo("Asia/Kolkata")


def today() -> date:
    """Today in India: price dates and cut-offs follow the hub's calendar, not UTC."""
    return datetime.now(IST).date()


async def current_list(session: AsyncSession, hub_id: uuid.UUID, on: date) -> PriceList | None:
    return (
        await session.execute(
            select(PriceList)
            .where(PriceList.hub_id == hub_id, PriceList.valid_from <= on)
            .order_by(PriceList.valid_from.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


@dataclass(frozen=True)
class HubPrice:
    price_paise: int
    mrp_paise: int | None
    hub_id: uuid.UUID
    valid_from: date


async def prices_for(
    session: AsyncSession,
    hub_id: uuid.UUID | None,
    product_ids: list[uuid.UUID],
    on: date | None = None,
) -> dict[uuid.UUID, HubPrice]:
    """Today's price of each product at the hub; products without one are left out."""
    if hub_id is None or not product_ids:
        return {}
    price_list = await current_list(session, hub_id, on or today())
    if price_list is None:
        return {}
    rows = await session.execute(
        select(Price).where(Price.price_list_id == price_list.id, Price.product_id.in_(product_ids))
    )
    return {
        price.product_id: HubPrice(
            price.price_paise, price.mrp_paise, price_list.hub_id, price_list.valid_from
        )
        for price in rows.scalars()
    }


async def price_list(
    session: AsyncSession, hub_id: uuid.UUID, on: date
) -> tuple[PriceList, list[Price]]:
    found = await current_list(session, hub_id, on)
    if found is None:
        raise ProblemError(
            404, "catalog.price_list_not_found", "This hub has no prices for that date yet."
        )
    prices = await session.execute(
        select(Price).where(Price.price_list_id == found.id).order_by(Price.product_id)
    )
    return found, list(prices.scalars())


async def save_price_list(
    session: AsyncSession,
    hub_id: uuid.UUID,
    valid_from: date,
    items: list[PriceItem],
    *,
    published_by: uuid.UUID | None,
    data_source: str = "real",
) -> PriceList:
    """Write a list (replacing the prices of one with the same date). No checks: callers check."""
    found = (
        await session.execute(
            select(PriceList).where(PriceList.hub_id == hub_id, PriceList.valid_from == valid_from)
        )
    ).scalar_one_or_none()
    if found is None:
        found = PriceList(hub_id=hub_id, valid_from=valid_from)
        session.add(found)
    found.published_by = published_by
    found.data_source = data_source
    await session.flush()
    await session.execute(delete(Price).where(Price.price_list_id == found.id))
    session.add_all(
        Price(
            price_list_id=found.id,
            product_id=item.product_id,
            price_paise=item.price_paise,
            mrp_paise=item.mrp_paise,
        )
        for item in items
    )
    await session.flush()
    return found


async def publish_price_list(
    session: AsyncSession, hub_id: uuid.UUID, body: PriceListIn, published_by: uuid.UUID
) -> PriceList:
    """A hub manager's new list (POST /v1/hubs/{hub_id}/price-lists), with every contract check."""
    if body.valid_from < today():
        raise ProblemError(
            422, "catalog.valid_from_past", "A price list starts today or later (India time)."
        )
    await check_items(session, body.items)
    exists = ProblemError(
        409, "catalog.price_list_exists", "This hub already has a price list for that date."
    )
    if await _has_list(session, hub_id, body.valid_from):
        raise exists
    try:
        # A savepoint, so two managers publishing the same date at once get a clean 409.
        async with session.begin_nested():
            created = await save_price_list(
                session, hub_id, body.valid_from, body.items, published_by=published_by
            )
    except IntegrityError:
        raise exists from None

    await _published(session, created, len(body.items), replaced=False)
    await audit.record(
        session,
        actor_user_id=published_by,
        action="catalog.price_list_published",
        entity_type="price_list",
        entity_id=str(created.id),
        after={
            "hub_id": str(hub_id),
            "valid_from": body.valid_from.isoformat(),
            "items_count": len(body.items),
        },
    )
    return created


async def check_items(session: AsyncSession, items: list[PriceItem]) -> None:
    """Prices at most the MRP, for products that exist and are on sale (§3.1)."""
    above = [str(i.product_id) for i in items if i.mrp_paise and i.price_paise > i.mrp_paise]
    if above:
        raise ProblemError(
            422,
            "catalog.price_above_mrp",
            "A price cannot be above the MRP.",
            extra={"product_ids": above},
        )
    wanted = {item.product_id for item in items}
    active = set(
        (
            await session.execute(select(Product.id).where(Product.id.in_(wanted), Product.active))
        ).scalars()
    )
    if unknown := sorted(str(product_id) for product_id in wanted - active):
        raise ProblemError(
            422,
            "catalog.product_unknown",
            "Some products do not exist or are not on sale.",
            extra={"product_ids": unknown},
        )


async def _published(
    session: AsyncSession, price_list: PriceList, items_count: int, *, replaced: bool
) -> None:
    await publish(
        session,
        events.PRICES_PUBLISHED,
        {
            "price_list_id": str(price_list.id),
            "hub_id": str(price_list.hub_id),
            "valid_from": price_list.valid_from.isoformat(),
            "items_count": items_count,
            "replaced": replaced,
        },
    )


async def _has_list(session: AsyncSession, hub_id: uuid.UUID, valid_from: date) -> bool:
    found = await session.execute(
        select(PriceList.id).where(PriceList.hub_id == hub_id, PriceList.valid_from == valid_from)
    )
    return found.first() is not None


# --- the console's price lists (spec §3.3) -------------------------------------------------------


def price_list_not_found() -> ProblemError:
    return ProblemError(404, "catalog.price_list_not_found", "This hub has no such price list.")


def _locked() -> ProblemError:
    return ProblemError(
        409,
        "catalog.price_list_locked",
        "This list has taken effect; orders and cycles were priced from it. Publish a new date.",
    )


async def list_lists(
    session: AsyncSession, hub_id: uuid.UUID, start: date | None, end: date | None
) -> list[tuple[PriceList, int]]:
    """A hub's lists with their item counts, newest first; by default the last 30 days and every
    future list."""
    count = (
        select(func.count())
        .where(Price.price_list_id == PriceList.id)
        .correlate(PriceList)
        .scalar_subquery()
    )
    stmt = select(PriceList, count).where(
        PriceList.hub_id == hub_id,
        PriceList.valid_from >= (start or today() - timedelta(days=30)),
    )
    if end is not None:
        stmt = stmt.where(PriceList.valid_from <= end)
    rows = await session.execute(stmt.order_by(PriceList.valid_from.desc()))
    return [(found, items) for found, items in rows.all()]


async def get_list(
    session: AsyncSession, hub_id: uuid.UUID, list_id: uuid.UUID, *, lock: bool = False
) -> tuple[PriceList, list[tuple[Price, Product]]]:
    stmt = select(PriceList).where(PriceList.id == list_id, PriceList.hub_id == hub_id)
    found = (await session.execute(stmt.with_for_update() if lock else stmt)).scalar_one_or_none()
    if found is None:
        raise price_list_not_found()
    rows = await session.execute(
        select(Price, Product)
        .join(Product, Product.id == Price.product_id)
        .where(Price.price_list_id == list_id)
        .order_by(Product.names["en"].astext)
    )
    return found, [(price, product) for price, product in rows.all()]


def _items_view(items: list[tuple[uuid.UUID, int, int | None]]) -> list[dict[str, Any]]:
    return sorted(
        ({"product_id": str(p), "price_paise": price, "mrp_paise": mrp} for p, price, mrp in items),
        key=lambda item: item["product_id"],
    )


async def replace_list(
    session: AsyncSession,
    hub_id: uuid.UUID,
    list_id: uuid.UUID,
    expected: int,
    items: list[PriceItem],
    actor: uuid.UUID,
) -> PriceList:
    """Replace every item of today's or a future list (PUT). Lines already on a cycle or an order
    keep their price: M3 and M4 freeze them."""
    found, current = await get_list(session, hub_id, list_id, lock=True)
    if found.valid_from < today():
        raise _locked()
    if found.version != expected:
        raise ProblemError(
            412, "catalog.version_conflict", "Someone else saved this price list; reload it."
        )
    await check_items(session, items)
    before = _items_view([(p.product_id, p.price_paise, p.mrp_paise) for p, _ in current])
    after = _items_view([(i.product_id, i.price_paise, i.mrp_paise) for i in items])
    if before == after:
        return found  # nothing to save: same version, no audit entry
    await session.execute(delete(Price).where(Price.price_list_id == found.id))
    session.add_all(
        Price(
            price_list_id=found.id,
            product_id=i.product_id,
            price_paise=i.price_paise,
            mrp_paise=i.mrp_paise,
        )
        for i in items
    )
    found.version += 1
    found.updated_by = actor
    await session.flush()
    await audit.record(
        session,
        actor_user_id=actor,
        action="catalog.price_list_updated",
        entity_type="price_list",
        entity_id=str(found.id),
        before={"items": before},
        after={"items": after},
    )
    await _published(session, found, len(items), replaced=True)
    await session.refresh(found)  # server-side updated_at
    return found


async def delete_list(
    session: AsyncSession, hub_id: uuid.UUID, list_id: uuid.UUID, actor: uuid.UUID
) -> None:
    """Delete a list that has not taken effect yet (valid_from after today)."""
    found, current = await get_list(session, hub_id, list_id, lock=True)
    if found.valid_from <= today():
        raise _locked()
    before = {
        "hub_id": str(hub_id),
        "valid_from": found.valid_from.isoformat(),
        "items": _items_view([(p.product_id, p.price_paise, p.mrp_paise) for p, _ in current]),
    }
    await session.execute(delete(PriceList).where(PriceList.id == found.id))
    await audit.record(
        session,
        actor_user_id=actor,
        action="catalog.price_list_deleted",
        entity_type="price_list",
        entity_id=str(found.id),
        before=before,
    )
