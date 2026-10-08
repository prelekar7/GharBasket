"""Simulator v1b: the weeks through M3, M4 and M5 (`--write-db`)."""

import uuid
from datetime import date

import pytest
from gharbasket_sim import through_modules as through
from gharbasket_sim.pantry import Line
from gharbasket_sim.run import simulate_world
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.orders.models import Order
from gharbasket.modules.subscriptions.models import (
    EditFeedback,
    PantryFeedback,
    Subscription,
    WeeklyCycle,
)
from gharbasket.platform.models import OutboxEvent
from gharbasket.tools.seed import seed_catalog, seed_delivery, seed_households, seed_societies

pytestmark = [pytest.mark.anyio, pytest.mark.services]


def line(product: str, qty: int, replaces: str | None = None) -> Line:
    return Line(product, qty, 250, 2000, "meal_plan", "ai", replaces)


def test_ops_turn_the_draft_into_the_household_box() -> None:
    ids = {slug: uuid.uuid5(uuid.NAMESPACE_URL, slug) for slug in ("a", "b", "c", "d", "e")}
    before = [line("a", 2), line("b", 1), line("c", 3)]
    after = [line("a", 4), line("d", 1, replaces="b"), line("e", 2)]

    ops = through.ops_between(before, after, ids)

    assert [(op.op, op.product_id, op.qty, op.substitute_id) for op in ops] == [
        ("substitute", ids["b"], None, ids["d"]),
        ("qty", ids["a"], 4, None),
        ("remove", ids["c"], None, None),
        ("add", ids["e"], 2, None),
    ]
    assert through.ops_between(before, before, ids) == []


async def test_a_week_goes_through_subscriptions_orders_and_billing(
    db_session: AsyncSession,
) -> None:
    await seed_societies(db_session)
    await seed_households(db_session)
    await seed_catalog(db_session)
    await seed_delivery(db_session)

    run = await simulate_world(
        db_session,
        seed=7,
        weeks=1,
        start=date(2026, 10, 5),
        planner="rules",
        adoption=0.02,
        write_db=True,
    )

    assert run.rows.outcomes and len(run.rows.outcomes) == len(run.households)
    subscribed = (
        await db_session.execute(select(func.count()).select_from(Subscription))
    ).scalar_one()
    assert subscribed >= len(run.households)
    cycles = (await db_session.execute(select(WeeklyCycle))).scalars().all()
    assert len(cycles) == len(run.households)
    statuses = {str(c.id): c.status for c in cycles}
    for outcome in run.rows.outcomes:
        assert outcome["cycle_id"] in statuses
        assert statuses[outcome["cycle_id"]] == outcome["cycle_status"]
        assert outcome["cycle_status"] in ("locked", "skipped", "cancelled")
    delivered = [o for o in run.rows.outcomes if o["order_status"] == "delivered"]
    assert delivered, run.rows.outcomes
    orders = (await db_session.execute(select(Order))).scalars().all()
    assert {str(o.id) for o in orders} >= {
        o["order_id"] for o in run.rows.outcomes if o["order_id"]
    }
    assert all(o.data_source == "synthetic" for o in orders)
    edits = (await db_session.execute(select(func.count()).select_from(EditFeedback))).scalar_one()
    assert edits == sum(o["ops"] for o in run.rows.outcomes) >= 0
    taps = (await db_session.execute(select(func.count()).select_from(PantryFeedback))).scalar_one()
    assert taps == sum(o["feedback"] for o in run.rows.outcomes)
    assert not any("subscriptions.version_conflict" in o["refused"] for o in run.rows.outcomes)
    # The cycles followed their orders through M3's outbox consumers, not by hand.
    unsent = await db_session.execute(
        select(func.count())
        .select_from(OutboxEvent)
        .where(
            OutboxEvent.event_type.in_(("orders.locked", "orders.cancelled")),
            OutboxEvent.sent_at.is_(None),
        )
    )
    assert unsent.scalar_one() == 0
