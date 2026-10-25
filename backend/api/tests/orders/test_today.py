"""orders.api.open_issue_summary and today_counts: the numbers behind M12's today board
(M12 §3.3)."""

import dataclasses
import uuid
from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.orders.issues import CALLBACK_MINUTES
from gharbasket.modules.orders.models import Order, OrderIssue, Slot

pytestmark = [pytest.mark.anyio, pytest.mark.services]

TUESDAY = date(2026, 11, 3)
HUB = uuid.uuid4()


async def _slot(session: AsyncSession) -> Slot:
    slot = Slot(hub_id=HUB, label="Morning", start_time=time(7), end_time=time(9), capacity=50)
    session.add(slot)
    await session.flush()
    return slot


def _order(slot: Slot, society: uuid.UUID, **values: object) -> Order:
    defaults: dict[str, object] = {
        "household_id": uuid.uuid4(),
        "hub_id": HUB,
        "society_id": society,
        "delivery_date": TUESDAY,
        "slot_id": slot.id,
        "cutoff_at": datetime(2026, 11, 2, 15, 30, tzinfo=UTC),
        "subtotal_paise": 60_000,
        "total_paise": 60_000,
        "kind": "box",
        "status": "locked",
    }
    return Order(**(defaults | values))


def _issue(order: Order, created_at: datetime, **values: object) -> OrderIssue:
    defaults: dict[str, object] = {
        "order_id": order.id,
        "household_id": order.household_id,
        "hub_id": order.hub_id,
        "kind": "missing",
        "photo_keys": [],
        "amount_paise": 5_000,
        "created_by": uuid.uuid4(),
        "created_at": created_at,
        "status": "open",
        "open_reason": "triage",
    }
    return OrderIssue(**(defaults | values))


async def test_the_open_issue_summary_counts_this_hubs_open_issues(
    db_session: AsyncSession,
) -> None:
    empty = await orders_api.open_issue_summary(db_session, hub_id=HUB)
    assert empty == orders_api.OpenIssueSummary(0, None, None)

    slot = await _slot(db_session)
    order = _order(slot, uuid.uuid4())
    elsewhere = _order(slot, uuid.uuid4(), hub_id=uuid.uuid4())
    db_session.add_all([order, elsewhere])
    await db_session.flush()
    oldest = datetime(2026, 11, 3, 6, 0, tzinfo=UTC)
    db_session.add_all(
        [
            _issue(order, oldest + timedelta(hours=3)),
            _issue(order, oldest),
            _issue(order, oldest - timedelta(days=1), status="credited", open_reason=None),
            _issue(elsewhere, oldest - timedelta(days=2), hub_id=elsewhere.hub_id),
        ]
    )
    await db_session.flush()

    summary = await orders_api.open_issue_summary(db_session, hub_id=HUB)

    assert summary == orders_api.OpenIssueSummary(
        count=2,
        oldest_created_at=oldest,
        earliest_callback_due_at=oldest + timedelta(minutes=CALLBACK_MINUTES),
    )
    assert {f.name for f in dataclasses.fields(summary)} == {
        "count",
        "oldest_created_at",
        "earliest_callback_due_at",
    }


async def test_today_counts_split_orders_by_status_and_boxes_by_society(
    db_session: AsyncSession,
) -> None:
    slot = await _slot(db_session)
    a, b = uuid.uuid4(), uuid.uuid4()
    db_session.add_all(
        [
            _order(slot, a),
            _order(slot, a, status="packed"),
            _order(slot, b, status="delivered"),
            _order(slot, b, status="payment_failed"),
            _order(slot, b, status="cancelled"),  # not a box of the day
            _order(slot, b, kind="addon", status="locked"),  # not a box
            _order(slot, a, hub_id=uuid.uuid4()),  # another hub
            _order(slot, a, delivery_date=TUESDAY + timedelta(days=1)),  # another day
        ]
    )
    await db_session.flush()

    counts = await orders_api.today_counts(db_session, hub_id=HUB, on=TUESDAY)

    assert counts.by_status == {
        "locked": 2,
        "packed": 1,
        "delivered": 1,
        "payment_failed": 1,
        "cancelled": 1,
    }
    assert counts.boxes_total == 4  # two in A, two in B (delivered and payment failed)
    assert counts.boxes_locked == 3  # locked, packed, delivered; payment_failed is not locked
    assert counts.boxes_by_society == {a: 2, b: 2}
    assert counts.cutoff_at == datetime(2026, 11, 2, 15, 30, tzinfo=UTC)  # 21:00 IST the day before

    quiet = await orders_api.today_counts(db_session, hub_id=uuid.uuid4(), on=TUESDAY)
    assert (quiet.by_status, quiet.boxes_total, quiet.boxes_locked) == ({}, 0, 0)
