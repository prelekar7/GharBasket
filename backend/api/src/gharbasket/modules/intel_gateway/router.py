"""HTTP endpoints of M10's gateway: internal endpoints that training jobs call with the operator
token (ADR-0025). Customers never call these.
"""

import uuid
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import AwareDatetime, BaseModel, Field

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


class ModelMetricIn(BaseModel):
    model_name: str = Field(min_length=1, max_length=60)
    version: str = Field(min_length=1, max_length=40)
    metric: str = Field(min_length=1, max_length=40)
    value: float
    split: Literal["train", "holdout", "backtest", "live"]
    data_source: Literal["synthetic", "pilot", "live", "mixed"]
    details: dict[str, Any] | None = None


class ModelMetricsIn(BaseModel):
    items: list[ModelMetricIn] = Field(min_length=1, max_length=2000)


class ModelMetricsOut(BaseModel):
    accepted: int


@router.post(
    "/internal/intel/model-metrics",
    summary="Store an evaluation run's metrics (the eval harness, training jobs; operator token)",
)
async def put_model_metrics(body: ModelMetricsIn, session: DbSession) -> ModelMetricsOut:
    accepted = 0
    for item in body.items:
        rows = await service.record_metrics(
            session,
            model_name=item.model_name,
            version=item.version,
            metrics={item.metric: item.value},
            split=item.split,
            data_source=item.data_source,
            details=item.details,
        )
        accepted += len(rows)
    await session.commit()
    return ModelMetricsOut(accepted=accepted)


class FeatureSnapshotIn(BaseModel):
    subject_type: Literal["household", "hub", "society", "order", "partner", "product"]
    subject_id: uuid.UUID
    features: dict[str, Any]


class FeatureSnapshotsIn(BaseModel):
    as_of: AwareDatetime  # an instant: a naive time would be read in the server's zone
    items: list[FeatureSnapshotIn] = Field(min_length=1, max_length=5000)


class FeatureSnapshotsOut(BaseModel):
    accepted: int


@router.post(
    "/internal/intel/feature-snapshots",
    summary="Store point-in-time features as of a cut-off (the snapshot job; operator token)",
)
async def put_feature_snapshots(
    body: FeatureSnapshotsIn, session: DbSession
) -> FeatureSnapshotsOut:
    for item in body.items:
        await service.save_features(
            session,
            subject_type=item.subject_type,
            subject_id=item.subject_id,
            as_of=body.as_of,
            features=item.features,
        )
    await session.commit()
    return FeatureSnapshotsOut(accepted=len(body.items))


class ForecastItemIn(BaseModel):
    product_id: uuid.UUID
    for_date: date
    committed: float = Field(ge=0)
    uncommitted_q50: float = Field(ge=0)
    uncommitted_qtau: float = Field(ge=0)
    order_qty: float = Field(ge=0)
    model: str = Field(max_length=40)


class ForecastsIn(BaseModel):
    hub_id: uuid.UUID
    model: str = Field(min_length=1, max_length=60)
    data_source: Literal["synthetic", "pilot", "live"] = Field(
        description="Whose orders the forecast was trained on; sources are never pooled"
    )
    items: list[ForecastItemIn] = Field(min_length=1, max_length=5000)


class ForecastsOut(BaseModel):
    accepted: int
    delivery_dates: list[date]


@router.post(
    "/internal/intel/forecasts",
    summary="Store a hub's demand forecast and order quantities (the forecast job; operator token)",
)
async def put_forecasts(body: ForecastsIn, session: DbSession) -> ForecastsOut:
    """One `intel.predictions` row per delivery day (kind `forecast`, subject the hub, `for_date`
    the delivery day) with every product's lines in the payload, and `intel.forecast_ready`;
    M6 reads it through `intel_gateway.api.hub_forecast`."""
    days = await service.record_forecasts(
        session,
        hub_id=body.hub_id,
        model_name=body.model,
        data_source=body.data_source,
        items=[item.model_dump(mode="json") for item in body.items],
    )
    await session.commit()
    return ForecastsOut(accepted=len(body.items), delivery_dates=days)
