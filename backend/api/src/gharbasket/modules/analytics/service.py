"""Business logic of M12 Console & Analytics: the AI health view over M10's metrics (the KPI,
unit-economics, cohort and impact views follow with their materialised views)."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import httpx2
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.analytics.schemas import (
    AiHealthOut,
    HubDriftOut,
    MetricOut,
    MlServiceOut,
    ModelHealthOut,
    PredictionCountOut,
    RegistrySyncOut,
    ServedModelOut,
)
from gharbasket.modules.intel_gateway import api as intel_api
from gharbasket.platform.settings import Settings

WINDOW_DAYS = 7


@asynccontextmanager
async def http_client(given: httpx2.AsyncClient | None) -> AsyncIterator[httpx2.AsyncClient]:
    """The app's shared client, or a short-lived one (tools and tests without a lifespan)."""
    if given is not None:
        yield given
        return
    async with httpx2.AsyncClient() as client:
        yield client


def _ml_service(status: intel_api.MlServiceStatus) -> MlServiceOut:
    sync = status.registry_sync
    return MlServiceOut(
        status=status.status,
        served=[ServedModelOut(model=m.model, version=m.version) for m in status.served],
        registry_sync=None
        if sync is None
        else RegistrySyncOut(
            last_success_at=sync.last_success_at,
            last_failure_at=sync.last_failure_at,
            last_error=sync.last_error,
        ),
        checked_at=status.checked_at,
    )


async def ai_health(
    session: AsyncSession, http: httpx2.AsyncClient, settings: Settings
) -> AiHealthOut:
    now = datetime.now(UTC)
    # The probe never touches the session, so it waits for the ML service while the database reads.
    ml_service, (models, counts) = await asyncio.gather(
        intel_api.ml_service_status(http, settings),
        intel_api.model_health(session, since=now - timedelta(days=WINDOW_DAYS)),
    )
    return AiHealthOut(
        generated_at=now,
        window_days=WINDOW_DAYS,
        ml_service=_ml_service(ml_service),
        models=[
            ModelHealthOut(
                model_name=m.model_name,
                metrics=[
                    MetricOut(
                        metric=r.metric,
                        value=r.value,
                        split=r.split,  # pyright: ignore[reportArgumentType]
                        data_source=r.data_source,  # pyright: ignore[reportArgumentType]
                        version=r.version,
                        computed_at=r.computed_at,
                        hub_id=r.hub_id,
                    )
                    for r in m.metrics
                ],
                drift=m.drift,
                hubs=[
                    HubDriftOut(
                        hub_id=h.hub_id,
                        data_source=h.data_source,  # pyright: ignore[reportArgumentType]
                        drift=h.drift,
                        drifted_days=h.drifted_days,
                        scored_days=h.scored_days,
                        for_date=h.for_date,
                        wape=h.wape,
                        backtest_wape=h.backtest_wape,
                    )
                    for h in m.hubs
                ],
            )
            for m in models
        ],
        predictions=[
            PredictionCountOut(kind=c.kind, source=c.source, count=c.count)  # pyright: ignore[reportArgumentType]
            for c in counts
        ],
    )
