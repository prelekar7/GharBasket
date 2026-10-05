"""Business logic of M12 Console & Analytics: the AI health view over M10's metrics (the KPI,
unit-economics, cohort and impact views follow with their materialised views)."""

from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.analytics.schemas import (
    AiHealthOut,
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
                    )
                    for r in m.metrics
                ],
                drift=m.drift,
            )
            for m in models
        ],
        predictions=[
            PredictionCountOut(kind=c.kind, source=c.source, count=c.count)  # pyright: ignore[reportArgumentType]
            for c in counts
        ],
    )
