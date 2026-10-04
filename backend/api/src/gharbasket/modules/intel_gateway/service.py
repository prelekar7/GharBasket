"""Writing and reading `intel.*` (M10 §4): predictions, metrics, feature snapshots, experiments.

Called by the gateway after an ML answer (or a fallback), by training jobs through the API, and
by the console's AI health page. Nothing here calls the ML service.
"""

import uuid
from datetime import date, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.intel_gateway.models import (
    Experiment,
    FeatureSnapshot,
    ModelMetric,
    Prediction,
)


async def record_prediction(
    session: AsyncSession,
    *,
    kind: str,
    subject_type: str,
    subject_id: uuid.UUID,
    for_date: date,
    source: str,
    payload: dict[str, Any],
    model_name: str,
    model_version: str | None = None,
) -> Prediction:
    """Keep what the ML service (or the fallback) answered. Flushes; the caller commits."""
    prediction = Prediction(
        kind=kind,
        subject_type=subject_type,
        subject_id=subject_id,
        for_date=for_date,
        source=source,
        payload=payload,
        model_name=model_name,
        model_version=model_version,
        request_id=structlog.contextvars.get_contextvars().get("request_id"),
    )
    session.add(prediction)
    await session.flush()
    return prediction


async def latest_prediction(
    session: AsyncSession, *, kind: str, subject_id: uuid.UUID, for_date: date
) -> Prediction | None:
    """The newest answer of this kind for a subject and date (ids are time-ordered)."""
    return (
        await session.execute(
            select(Prediction)
            .where(
                Prediction.kind == kind,
                Prediction.subject_id == subject_id,
                Prediction.for_date == for_date,
            )
            .order_by(Prediction.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def record_metrics(
    session: AsyncSession,
    *,
    model_name: str,
    version: str,
    metrics: dict[str, float],
    split: str,
    data_source: str,
    details: dict[str, Any] | None = None,
) -> list[ModelMetric]:
    """One row per metric, all sharing the run's details. Flushes; the caller commits."""
    rows = [
        ModelMetric(
            model_name=model_name,
            version=version,
            metric=metric,
            value=float(value),
            split=split,
            data_source=data_source,
            details=details,
        )
        for metric, value in sorted(metrics.items())
    ]
    session.add_all(rows)
    await session.flush()
    return rows


async def latest_metrics(session: AsyncSession, model_name: str) -> list[ModelMetric]:
    """The newest value of each (metric, split, data_source) for a model."""
    rows = (
        await session.execute(
            select(ModelMetric)
            .where(ModelMetric.model_name == model_name)
            .order_by(ModelMetric.id.desc())
        )
    ).scalars()
    seen: dict[tuple[str, str, str], ModelMetric] = {}
    for row in rows:
        seen.setdefault((row.metric, row.split, row.data_source), row)
    return sorted(seen.values(), key=lambda m: (m.metric, m.split, m.data_source))


async def save_features(
    session: AsyncSession,
    *,
    subject_type: str,
    subject_id: uuid.UUID,
    as_of: datetime,
    features: dict[str, Any],
) -> None:
    """Point-in-time features; writing the same (subject, as_of) again replaces them."""
    await session.execute(
        insert(FeatureSnapshot)
        .values(subject_type=subject_type, subject_id=subject_id, as_of=as_of, features=features)
        .on_conflict_do_update(
            index_elements=[
                FeatureSnapshot.subject_type,
                FeatureSnapshot.subject_id,
                FeatureSnapshot.as_of,
            ],
            set_={"features": features},
        )
    )


async def assign_variant(
    session: AsyncSession,
    *,
    key: str,
    subject_type: str,
    subject_id: uuid.UUID,
    variants: tuple[str, ...],
) -> str:
    """A sticky variant per subject: hashed from (key, subject id), stored once."""
    if not variants:
        raise ValueError("at least one variant")
    bucket = uuid.uuid5(uuid.NAMESPACE_URL, f"{key}:{subject_id}").int % len(variants)
    await session.execute(
        insert(Experiment)
        .values(key=key, variant=variants[bucket], subject_type=subject_type, subject_id=subject_id)
        .on_conflict_do_nothing()
    )
    found = (
        await session.execute(
            select(Experiment.variant).where(
                Experiment.key == key,
                Experiment.subject_type == subject_type,
                Experiment.subject_id == subject_id,
            )
        )
    ).scalar_one()
    return found
