"""Facade of M2 Catalog & Pricing: the only module other modules may import."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog.models import Product


async def product_ids_by_slug(session: AsyncSession, slugs: list[str]) -> dict[str, uuid.UUID]:
    """Active products' ids by slug; unknown slugs are left out."""
    if not slugs:
        return {}
    rows = await session.execute(
        select(Product.slug, Product.id).where(Product.slug.in_(slugs), Product.active)
    )
    return {slug: product_id for slug, product_id in rows.all()}
