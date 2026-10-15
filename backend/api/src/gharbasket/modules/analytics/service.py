"""Business logic of M12 Console & Analytics: the AI health view over M10's metrics (the KPI,
unit-economics, cohort and impact views follow with their materialised views)."""

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.analytics.schemas import (
    AiHealthOut,
    HubDriftOut,
    MetricOut,
    ModelHealthOut,
    PredictionCountOut,
)
from gharbasket.modules.intel_gateway import api as intel_api

WINDOW_DAYS = 7


async def ai_health(session: AsyncSession) -> AiHealthOut:
    now = datetime.now(UTC)
    models, counts = await intel_api.model_health(session, since=now - timedelta(days=WINDOW_DAYS))
    return AiHealthOut(
        generated_at=now,
        window_days=WINDOW_DAYS,
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
