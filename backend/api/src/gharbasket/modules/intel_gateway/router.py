"""HTTP endpoints of M10's gateway: internal endpoints that training jobs call with the operator
token (ADR-0025). Customers never call these.
"""

from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from gharbasket.modules.catalog import api as catalog_api
from gharbasket.modules.intel_gateway import service
from gharbasket.platform.db import DbSession
from gharbasket.platform.operator import require_operator

router = APIRouter(tags=["intel_gateway"], dependencies=[Depends(require_operator)])


class PriceIndexItem(BaseModel):
    slug: str = Field(max_length=60)
    day: date
    market: str = Field(min_length=1, max_length=80)
    modal_paise_per_kg: int = Field(gt=0)
    index_4w: float = Field(gt=0, description="Today's price over the 4-week median; 1.0 = usual")


class PriceIndexIn(BaseModel):
    items: list[PriceIndexItem] = Field(max_length=5000)


class PriceIndexOut(BaseModel):
    accepted: int
    unknown_slugs: list[str]


@router.post(
    "/internal/intel/price-index",
    summary="Store today's wholesale price index per product (the mandi collector; operator token)",
)
async def put_price_index(body: PriceIndexIn, session: DbSession) -> PriceIndexOut:
    slugs = sorted({item.slug for item in body.items})
    ids = await catalog_api.product_ids_by_slug(session, slugs)
    accepted = await service.save_price_index(
        session, [item.model_dump() for item in body.items], ids
    )
    await session.commit()
    return PriceIndexOut(accepted=accepted, unknown_slugs=[s for s in slugs if s not in ids])
