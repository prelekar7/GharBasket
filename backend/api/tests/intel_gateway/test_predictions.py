"""intel.* writers (M10 §4) and the gateway keeping what it answered."""

import uuid
from datetime import UTC, date, datetime

import httpx2
import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.intel_gateway import api, service
from gharbasket.modules.intel_gateway.models import Experiment, FeatureSnapshot, Prediction

from ..conftest import make_settings
from .test_plan_week import plan_request

pytestmark = [pytest.mark.anyio, pytest.mark.services]


async def test_predictions_are_kept_newest_first(db_session: AsyncSession) -> None:
    household, monday = uuid.uuid4(), date(2026, 10, 12)

    first = await service.record_prediction(
        db_session,
        kind="weekly_plan",
        subject_type="household",
        subject_id=household,
        for_date=monday,
        source="fallback",
        payload={"plan": []},
        model_name="rules-v0",
    )
    second = await service.record_prediction(
        db_session,
        kind="weekly_plan",
        subject_type="household",
        subject_id=household,
        for_date=monday,
        source="ml",
        payload={"plan": [{"day": 0}]},
        model_name="cpsat-v0",
        model_version="2026.10.04-1",
    )

    latest = await service.latest_prediction(
        db_session, kind="weekly_plan", subject_id=household, for_date=monday
    )
    assert latest is not None and latest.id == second.id and latest.id > first.id
    assert latest.source == "ml" and latest.model_version == "2026.10.04-1"
    nothing = await service.latest_prediction(
        db_session, kind="pantry", subject_id=household, for_date=monday
    )
    assert nothing is None
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            db_session.add(
                Prediction(
                    kind="not_a_kind",
                    subject_type="household",
                    subject_id=household,
                    for_date=monday,
                    source="ml",
                    payload={},
                    model_name="x",
                )
            )
            await db_session.flush()


async def test_metrics_keep_every_run_and_report_the_latest_per_split(
    db_session: AsyncSession,
) -> None:
    for version, wape in (("v1", 0.31), ("v2", 0.24)):
        await service.record_metrics(
            db_session,
            model_name="forecast",
            version=version,
            metrics={"wape": wape, "mase": 0.9},
            split="backtest",
            data_source="synthetic",
            details={"seeds": [7, 11], "n": 1285},
        )
    await service.record_metrics(
        db_session,
        model_name="forecast",
        version="v2",
        metrics={"wape": 0.4},
        split="live",
        data_source="pilot",
    )

    latest = await service.latest_metrics(db_session, "forecast")

    assert [(m.metric, m.split, m.data_source, m.version, m.value) for m in latest] == [
        ("mase", "backtest", "synthetic", "v2", 0.9),
        ("wape", "backtest", "synthetic", "v2", 0.24),
        ("wape", "live", "pilot", "v2", 0.4),
    ]
    assert latest[0].details == {"seeds": [7, 11], "n": 1285}


async def test_feature_snapshots_replace_the_same_moment_and_experiments_stick(
    db_session: AsyncSession,
) -> None:
    household = uuid.uuid4()
    as_of = datetime(2026, 10, 11, 15, 30, tzinfo=UTC)

    await service.save_features(
        db_session,
        subject_type="household",
        subject_id=household,
        as_of=as_of,
        features={"weeks_active": 3},
    )
    await service.save_features(
        db_session,
        subject_type="household",
        subject_id=household,
        as_of=as_of,
        features={"weeks_active": 4},
    )
    rows = (await db_session.execute(select(FeatureSnapshot))).scalars().all()
    assert len(rows) == 1 and rows[0].features == {"weeks_active": 4}

    variants = ("rules", "planner_v1")
    first = await service.assign_variant(
        db_session,
        key="planner_v1_vs_rules",
        subject_type="household",
        subject_id=household,
        variants=variants,
    )
    again = await service.assign_variant(
        db_session,
        key="planner_v1_vs_rules",
        subject_type="household",
        subject_id=household,
        variants=("other",),
    )
    assert first == again and first in variants
    assert len((await db_session.execute(select(Experiment))).scalars().all()) == 1
    with pytest.raises(ValueError):
        await service.assign_variant(
            db_session, key="k", subject_type="household", subject_id=household, variants=()
        )


async def test_the_gateway_records_the_plan_it_answered_with(db_session: AsyncSession) -> None:
    household, monday = uuid.uuid4(), date(2026, 10, 12)
    http = httpx2.AsyncClient()  # never used: no ML service configured, so the rules answer
    try:
        plan = await api.plan_week(
            http,
            make_settings(),
            plan_request(),
            session=db_session,
            household_id=household,
            week_monday=monday,
        )
    finally:
        await http.aclose()

    kept = await api.latest_prediction(
        db_session, kind="weekly_plan", subject_id=household, for_date=monday
    )
    assert plan.source == "fallback"
    assert kept is not None
    assert (kept.source, kept.model_name, kept.subject_type) == (
        "fallback",
        "rules-v0",
        "household",
    )
    assert kept.payload["status"] == plan.status
    assert len(kept.payload["plan"]) == 21 and "source" not in kept.payload
