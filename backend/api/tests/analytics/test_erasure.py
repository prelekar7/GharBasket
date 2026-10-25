"""M12 §4, §9 erasure: after `identity.user_deleted`, the household's `fact_households` and
`fact_cycles` rows are gone (its orders stay, as M4 keeps them), never while a refresh is writing
facts, and the consumer is subscribed."""

import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import InstrumentedAttribute

from gharbasket.modules.analytics import jobs, refresh
from gharbasket.modules.analytics.models import FactCycle, FactHousehold, FactOrder
from gharbasket.modules.identity import api as identity_api
from gharbasket.platform import outbox
from gharbasket.platform.jobs import lock_key
from gharbasket.platform.models import OutboxEvent

pytestmark = [pytest.mark.anyio, pytest.mark.services]

TUESDAY = date(2026, 11, 3)
HUB, SOCIETY = uuid.uuid4(), uuid.uuid4()
NOW = datetime(2026, 11, 3, 4, 30, tzinfo=UTC)


def _facts(household_id: uuid.UUID) -> list[object]:
    return [
        FactHousehold(
            household_id=household_id,
            hub_id=HUB,
            society_id=SOCIETY,
            created_on=date(2026, 10, 20),
            cohort_week=date(2026, 10, 19),
            status="deleting",
            data_source="real",
        ),
        FactCycle(
            cycle_id=uuid.uuid4(),
            household_id=household_id,
            hub_id=HUB,
            society_id=SOCIETY,
            week_start=date(2026, 11, 2),
            delivery_date=TUESDAY,
            status="locked",
            auto_approved=False,
            approved_before_cutoff=True,
            edits=1,
            plan_source="ml",
            ran_out_taps=0,
            wasted_taps=0,
            just_right_taps=1,
            data_source="real",
        ),
        FactOrder(
            order_id=uuid.uuid4(),
            household_id=household_id,
            hub_id=HUB,
            society_id=SOCIETY,
            kind="box",
            delivery_date=TUESDAY,
            status="delivered",
            total_paise=60_000,
            fees_paise=0,
            refunded_paise=0,
            credited_paise=0,
            lines=10,
            short_lines=0,
            on_time=True,
            first_attempt=True,
            data_source="real",
        ),
    ]


def _event(household_id: uuid.UUID | None) -> OutboxEvent:
    return OutboxEvent(
        id=uuid.uuid4(),
        event_type=identity_api.USER_DELETED,
        payload={
            "user_id": str(uuid.uuid4()),
            "household_id": None if household_id is None else str(household_id),
        },
    )


async def _count(
    session: AsyncSession, column: InstrumentedAttribute[uuid.UUID], household_id: uuid.UUID
) -> int:
    return int(await session.scalar(select(func.count()).where(column == household_id)) or 0)


def test_the_consumer_is_subscribed() -> None:
    assert jobs.on_user_deleted in outbox.CONSUMERS[identity_api.USER_DELETED]


async def test_a_purged_household_leaves_its_household_and_cycle_facts(
    db_session: AsyncSession,
) -> None:
    gone, kept = uuid.uuid4(), uuid.uuid4()
    db_session.add_all([*_facts(gone), *_facts(kept)])
    await db_session.flush()

    await jobs.on_user_deleted(db_session, _event(gone))
    await jobs.on_user_deleted(db_session, _event(None))  # a partner-only account: nothing
    await jobs.on_user_deleted(db_session, _event(gone))  # a redelivered event: still nothing

    assert await _count(db_session, FactHousehold.household_id, gone) == 0
    assert await _count(db_session, FactCycle.household_id, gone) == 0
    assert await _count(db_session, FactOrder.household_id, gone) == 1  # kept, as M4 keeps it
    for column in (FactHousehold.household_id, FactCycle.household_id, FactOrder.household_id):
        assert await _count(db_session, column, kept) == 1


async def test_the_erasure_waits_for_a_refresh_in_progress(
    db_session: AsyncSession, database_url: str
) -> None:
    """A refresh that read the household before the purge must not write it back after the
    erasure: while one holds the lock, the event fails and the outbox retries it."""
    household = uuid.uuid4()
    db_session.add_all(_facts(household))
    await db_session.flush()
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as other, other.begin():
            await other.execute(
                text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key(refresh.LOCK)}
            )
            with pytest.raises(refresh.RefreshRunning):
                await jobs.on_user_deleted(db_session, _event(household))
    finally:
        await engine.dispose()
    assert await _count(db_session, FactHousehold.household_id, household) == 1

    await jobs.on_user_deleted(db_session, _event(household))  # the retry, the refresh done
    assert await _count(db_session, FactHousehold.household_id, household) == 0
