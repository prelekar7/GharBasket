"""Writing and reading `intel.*` (M10 §4): predictions, metrics, feature snapshots, experiments.

Called by the gateway after an ML answer (or a fallback), by training jobs through the API, and
by the console's AI health page. Nothing here calls the ML service.
"""

import uuid
from datetime import date, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.intel_gateway.models import (
    Experiment,
    FeatureSnapshot,
    ModelMetric,
    PantryPrior,
    Prediction,
    PriceIndex,
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


async def forecasts_on(session: AsyncSession, day: date) -> list[Prediction]:
    """The newest forecast per hub for a delivery day."""
    rows = (
        await session.execute(
            select(Prediction)
            .where(Prediction.kind == "forecast", Prediction.for_date == day)
            .order_by(Prediction.id.desc())
        )
    ).scalars()
    newest: dict[uuid.UUID, Prediction] = {}
    for row in rows:
        newest.setdefault(row.subject_id, row)
    return list(newest.values())


async def scored_already(
    session: AsyncSession, model_name: str, metric: str, day: date, hub_id: uuid.UUID | None
) -> bool:
    """Is there a live row of this metric for the day (and hub) already? Jobs rerun safely."""
    stmt = select(ModelMetric.id).where(
        ModelMetric.model_name == model_name,
        ModelMetric.metric == metric,
        ModelMetric.split == "live",
        ModelMetric.details["for_date"].astext == day.isoformat(),
    )
    if hub_id is not None:
        stmt = stmt.where(ModelMetric.details["hub_id"].astext == str(hub_id))
    return (await session.execute(stmt.limit(1))).scalar_one_or_none() is not None


async def model_names(session: AsyncSession) -> list[str]:
    rows = await session.execute(
        select(ModelMetric.model_name).distinct().order_by(ModelMetric.model_name)
    )
    return [row for (row,) in rows.all()]


async def prediction_counts(session: AsyncSession, since: datetime) -> list[tuple[str, str, int]]:
    """(kind, source, count) of predictions recorded since a time: how often the fallback
    answered instead of the ML service."""
    rows = await session.execute(
        select(Prediction.kind, Prediction.source, func.count())
        .where(Prediction.created_at >= since)
        .group_by(Prediction.kind, Prediction.source)
        .order_by(Prediction.kind, Prediction.source)
    )
    return [(kind, source, int(count)) for kind, source, count in rows.all()]


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


async def save_price_index(
    session: AsyncSession, rows: list[dict[str, Any]], ids: dict[str, uuid.UUID]
) -> int:
    """Upsert (product, day, market) rows from the mandi collector; returns how many were kept.

    `rows` carry `slug`, `day`, `market`, `modal_paise_per_kg`, `index_4w`; slugs not in `ids`
    (unknown products) are skipped.
    """
    kept = 0
    for row in rows:
        product_id = ids.get(row["slug"])
        if product_id is None:
            continue
        await session.execute(
            insert(PriceIndex)
            .values(
                product_id=product_id,
                day=row["day"],
                market=row["market"],
                modal_paise_per_kg=row["modal_paise_per_kg"],
                index_4w=row["index_4w"],
            )
            .on_conflict_do_update(
                index_elements=[PriceIndex.product_id, PriceIndex.day, PriceIndex.market],
                set_={
                    "modal_paise_per_kg": row["modal_paise_per_kg"],
                    "index_4w": row["index_4w"],
                    "updated_at": func.now(),
                },
            )
        )
        kept += 1
    return kept


async def save_pantry_priors(
    session: AsyncSession, rows: list[dict[str, Any]], ids: dict[str, uuid.UUID], version: str
) -> int:
    """Upsert (cohort, product) priors from the refit job; unknown slugs are skipped."""
    kept = 0
    for row in rows:
        product_id = ids.get(row["product"])
        if product_id is None:
            continue
        await session.execute(
            insert(PantryPrior)
            .values(
                cohort=row["cohort"],
                product_id=product_id,
                prior_mean=row["prior_mean"],
                prior_weeks=row["prior_weeks"],
                version=version,
            )
            .on_conflict_do_update(
                index_elements=[PantryPrior.cohort, PantryPrior.product_id],
                set_={
                    "prior_mean": row["prior_mean"],
                    "prior_weeks": row["prior_weeks"],
                    "version": version,
                    "updated_at": func.now(),
                },
            )
        )
        kept += 1
    return kept


async def pantry_priors(session: AsyncSession, cohort: str) -> dict[uuid.UUID, tuple[float, float]]:
    """{product_id: (prior_mean, prior_weeks)} learned for a cohort; empty until the job ran."""
    rows = await session.execute(
        select(PantryPrior.product_id, PantryPrior.prior_mean, PantryPrior.prior_weeks).where(
            PantryPrior.cohort == cohort
        )
    )
    return {pid: (mean, weeks) for pid, mean, weeks in rows.all()}


async def price_spikes(
    session: AsyncSession, product_ids: list[uuid.UUID], on: date
) -> dict[uuid.UUID, float]:
    """The latest index (within 7 days of `on`) per product, averaged over markets: 1.0 = usual.

    Products with no recent row are left out; the planner treats them as usual-priced.
    """
    if not product_ids:
        return {}
    rows = await session.execute(
        select(PriceIndex.product_id, PriceIndex.day, PriceIndex.index_4w).where(
            PriceIndex.product_id.in_(product_ids),
            PriceIndex.day <= on,
            PriceIndex.day >= on - timedelta(days=7),
        )
    )
    latest_day: dict[uuid.UUID, date] = {}
    values: dict[uuid.UUID, list[float]] = {}
    for product_id, day, index in rows.all():
        if product_id not in latest_day or day > latest_day[product_id]:
            latest_day[product_id], values[product_id] = day, [index]
        elif day == latest_day[product_id]:
            values[product_id].append(index)
    return {pid: round(sum(v) / len(v), 3) for pid, v in values.items()}
