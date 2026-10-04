"""Facade of M2 Catalog & Pricing: the only module other modules may import."""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog.models import Product
from gharbasket.modules.catalog.pricing import prices_for
from gharbasket.modules.catalog.service import step_label

__all__ = ["LineProduct", "line_products", "product_ids_by_slug", "step_label"]


async def product_ids_by_slug(session: AsyncSession, slugs: list[str]) -> dict[str, uuid.UUID]:
    """Active products' ids by slug; unknown slugs are left out."""
    if not slugs:
        return {}
    rows = await session.execute(
        select(Product.slug, Product.id).where(Product.slug.in_(slugs), Product.active)
    )
    return {slug: product_id for slug, product_id in rows.all()}


@dataclass(frozen=True)
class LineProduct:
    """What an order line needs from a product: its step, names and (optionally) hub price."""

    id: uuid.UUID
    slug: str
    names: dict[str, str]
    step_qty: int
    step_unit: str
    price_paise: int | None


async def line_products(
    session: AsyncSession, product_ids: list[uuid.UUID], hub_id: uuid.UUID | None = None
) -> dict[uuid.UUID, LineProduct]:
    """Active products by id, with today's price at `hub_id` when given; unknown ids left out."""
    if not product_ids:
        return {}
    rows = await session.execute(select(Product).where(Product.id.in_(product_ids), Product.active))
    products = list(rows.scalars())
    prices = await prices_for(session, hub_id, [p.id for p in products]) if hub_id else {}
    return {
        p.id: LineProduct(
            p.id,
            p.slug,
            dict(p.names),
            p.step_qty,
            p.step_unit,
            prices[p.id].price_paise if p.id in prices else None,
        )
        for p in products
    }
