"""Substitutes for a product (spec §3.1, US-2): rule-based v0, ranked, never unsafe.

Score (0 to 1) = 0.5 for the same category + 0.4 times the tag overlap (Jaccard) + up to 0.1 for a
similar price. A substitute never adds something the original avoids: a root vegetable, onion or
garlic (Jain households), egg or dairy. Learned scores from the ML service replace this later.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.catalog.models import Product
from gharbasket.modules.catalog.pricing import HubPrice, prices_for
from gharbasket.modules.catalog.service import product_not_found

# Tags a substitute may only carry if the original does too.
RESTRICTED_TAGS = frozenset({"root", "onion_garlic", "egg", "dairy"})


@dataclass(frozen=True)
class Candidate:
    product: Product
    score: float
    price: HubPrice | None
    price_delta_paise: int | None
    reason_code: str
    reason_params: dict[str, int | str]


def _jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def _rank(original: Product, other: Product, prices: dict[uuid.UUID, HubPrice]) -> Candidate | None:
    own_tags, other_tags = set(original.tags), set(other.tags)
    if (other_tags & RESTRICTED_TAGS) - own_tags:
        return None
    same_category = other.category_id == original.category_id
    overlap = _jaccard(own_tags, other_tags)
    if not same_category and overlap == 0:
        return None

    base, price = prices.get(original.id), prices.get(other.id)
    delta: int | None = None
    closeness = 0.05  # unknown prices count as neither close nor far
    if base is not None and price is not None:
        delta = price.price_paise - base.price_paise
        closeness = 0.1 * max(0.0, 1 - abs(delta) / base.price_paise)
    score = round(0.5 * same_category + 0.4 * overlap + closeness, 3)

    params: dict[str, int | str] = {}
    if same_category and delta is not None and delta < 0:
        reason = "cheaper"
        params["saves_paise"] = -delta
    elif same_category:
        reason = "same_family"
    else:
        reason = "similar_use"
    return Candidate(other, score, price, delta, reason, params)


async def substitutes(
    session: AsyncSession, product_id: uuid.UUID, hub_id: uuid.UUID | None, limit: int
) -> list[Candidate]:
    original = await session.get(Product, product_id)
    if original is None or not original.active:
        raise product_not_found()
    others = list(
        (
            await session.execute(
                select(Product).where(
                    Product.active, Product.kind == original.kind, Product.id != original.id
                )
            )
        ).scalars()
    )
    prices = await prices_for(session, hub_id, [original.id, *(p.id for p in others)])
    ranked = [c for p in others if (c := _rank(original, p, prices)) is not None]
    ranked.sort(key=lambda c: (-c.score, c.product.slug))
    return ranked[:limit]
