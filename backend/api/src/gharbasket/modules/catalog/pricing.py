"""Hub price lists (spec §3.1, US-3): prices per step, in paise, from a `valid_from` date.

The current list of a hub on a date is the newest one with `valid_from` on or before it. Lists are
never edited in place: a hub manager publishes a new list for a new date, so past orders keep the
prices they were made with.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
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
    above = [str(i.product_id) for i in body.items if i.mrp_paise and i.price_paise > i.mrp_paise]
    if above:
        raise ProblemError(
            422,
            "catalog.price_above_mrp",
            "A price cannot be above the MRP.",
            extra={"product_ids": above},
        )
    wanted = {item.product_id for item in body.items}
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

    await publish(
        session,
        events.PRICES_PUBLISHED,
        {
            "price_list_id": str(created.id),
            "hub_id": str(hub_id),
            "valid_from": body.valid_from.isoformat(),
            "items_count": len(body.items),
        },
    )
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


async def _has_list(session: AsyncSession, hub_id: uuid.UUID, valid_from: date) -> bool:
    found = await session.execute(
        select(PriceList.id).where(PriceList.hub_id == hub_id, PriceList.valid_from == valid_from)
    )
    return found.first() is not None
