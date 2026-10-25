"""subscriptions.api.cycle_facts: M12's acceptance rows (M12 §3.1)."""

import uuid
from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.subscriptions import api as subs_api
from gharbasket.modules.subscriptions.models import (
    EditFeedback,
    PantryFeedback,
    Plan,
    Subscription,
    WeeklyCycle,
)

pytestmark = [pytest.mark.anyio, pytest.mark.services]

TUESDAY = date(2026, 11, 3)
CUTOFF = datetime(2026, 11, 2, 15, 30, tzinfo=UTC)  # 21:00 IST the day before
HUB = uuid.uuid4()


async def _cycle(
    session: AsyncSession, delivery: date, *, approved_at: datetime | None, **values: object
) -> WeeklyCycle:
    household = uuid.uuid4()
    plan_id = (await session.execute(select(Plan.id).where(Plan.code == "autopilot"))).scalar_one()
    subscription = Subscription(household_id=household, plan_id=plan_id, payment_mode="wallet")
    session.add(subscription)
    await session.flush()
    cycle = WeeklyCycle(
        household_id=household,
        hub_id=HUB,
        society_id=uuid.uuid4(),
        subscription_id=subscription.id,
        week_start=date(2026, 11, 2),
        delivery_date=delivery,
        slot_id=uuid.uuid4(),
        cutoff_at=CUTOFF,
        plan_source="ml",
        approved_at=approved_at,
        **{"status": "locked", **values},
    )
    session.add(cycle)
    await session.flush()
    return cycle


async def test_cycle_facts_count_line_edits_and_pantry_taps(db_session: AsyncSession) -> None:
    on_time = await _cycle(db_session, TUESDAY, approved_at=CUTOFF, auto_approved=True)
    late = await _cycle(
        db_session,
        TUESDAY,
        approved_at=datetime(2026, 11, 2, 16, 0, tzinfo=UTC),
        data_source="synthetic",
    )
    skipped = await _cycle(db_session, TUESDAY, approved_at=None, status="skipped")
    await _cycle(db_session, date(2026, 11, 10), approved_at=None)  # outside the window
    product = uuid.uuid4()
    db_session.add_all(
        [
            EditFeedback(cycle_id=on_time.id, product_id=product, action="removed"),
            EditFeedback(cycle_id=on_time.id, product_id=uuid.uuid4(), action="qty_up"),
            EditFeedback(
                cycle_id=on_time.id, action="meal_swapped", meal_date=TUESDAY, slot="dinner"
            ),
            PantryFeedback(
                cycle_id=on_time.id,
                household_id=on_time.household_id,
                product_id=product,
                signal="ran_out",
            ),
            PantryFeedback(
                cycle_id=on_time.id,
                household_id=on_time.household_id,
                product_id=uuid.uuid4(),
                signal="wasted",
            ),
            PantryFeedback(
                cycle_id=on_time.id,
                household_id=on_time.household_id,
                product_id=uuid.uuid4(),
                signal="just_right",
            ),
        ]
    )
    await db_session.flush()

    facts = await subs_api.cycle_facts(
        db_session, date_from=TUESDAY, date_to=TUESDAY, hub_ids=[HUB]
    )

    by_id = {f.cycle_id: f for f in facts}
    assert set(by_id) == {on_time.id, late.id, skipped.id}
    first = by_id[on_time.id]
    assert (first.auto_approved, first.approved_before_cutoff, first.edits) == (True, True, 2)
    assert (first.ran_out_taps, first.wasted_taps, first.just_right_taps) == (1, 1, 1)
    assert (first.plan_source, first.status, first.data_source) == ("ml", "locked", "real")
    assert (by_id[late.id].approved_before_cutoff, by_id[late.id].data_source) == (
        False,
        "synthetic",
    )
    assert (by_id[skipped.id].approved_before_cutoff, by_id[skipped.id].edits) == (False, 0)
    assert (
        await subs_api.cycle_facts(
            db_session, date_from=TUESDAY, date_to=TUESDAY, hub_ids=[uuid.uuid4()]
        )
        == []
    )


async def test_cycle_facts_count_feedback_of_the_window_only(db_session: AsyncSession) -> None:
    """The edit and tap counts are taken over the window's cycles (by their cycle-id indexes), not
    grouped over the whole feedback tables on every 15-minute run."""
    statements: list[str] = []

    def record(_conn: Any, _cursor: Any, statement: str, *_: Any) -> None:
        if "edit_feedback" in statement:
            statements.append(statement)

    assert db_session.bind is not None
    engine = db_session.bind.sync_engine  # type: ignore[union-attr]
    event.listen(engine, "before_cursor_execute", record)
    try:
        await subs_api.cycle_facts(db_session, date_from=TUESDAY, date_to=TUESDAY)
    finally:
        event.remove(engine, "before_cursor_execute", record)

    [statement] = statements
    for table in ("edit_feedback", "pantry_feedback"):
        grouped = statement.split(f"FROM subscriptions.{table}", 1)[1].split("GROUP BY", 1)[0]
        assert "weekly_cycles.delivery_date >=" in grouped, grouped
