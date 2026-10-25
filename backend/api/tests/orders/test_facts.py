"""orders.api.order_facts and issue_facts: M12's order and issue rows (M12 §3.1)."""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.orders.models import (
    CarryOver,
    Order,
    OrderEvent,
    OrderIssue,
    OrderLine,
    Slot,
)

pytestmark = [pytest.mark.anyio, pytest.mark.services]

IST = ZoneInfo("Asia/Kolkata")
TUESDAY = date(2026, 11, 3)
HUB = uuid.uuid4()


async def _slot(session: AsyncSession) -> Slot:
    slot = Slot(hub_id=HUB, label="Morning", start_time=time(7), end_time=time(9), capacity=50)
    session.add(slot)
    await session.flush()
    return slot


def _order(slot: Slot, delivery: date = TUESDAY, **values: object) -> Order:
    return Order(
        household_id=uuid.uuid4(),
        hub_id=HUB,
        society_id=uuid.uuid4(),
        delivery_date=delivery,
        slot_id=slot.id,
        cutoff_at=datetime(2026, 11, 2, 15, 30, tzinfo=UTC),
        subtotal_paise=60_000,
        **{"kind": "box", "status": "delivered", "fees_paise": 3_000, "total_paise": 63_000}
        | values,
    )


def _issue(order: Order, **values: object) -> OrderIssue:
    return OrderIssue(
        order_id=order.id,
        household_id=order.household_id,
        hub_id=HUB,
        kind="missing",
        photo_keys=[],
        amount_paise=5_000,
        created_by=uuid.uuid4(),
        **values,
    )


async def test_order_facts_add_up_lines_money_and_the_trips(db_session: AsyncSession) -> None:
    slot = await _slot(db_session)
    box = _order(slot)
    refunded = _order(slot, status="refunded", data_source="synthetic")
    later = _order(slot, TUESDAY + timedelta(days=7))
    db_session.add_all([box, refunded, later])
    await db_session.flush()
    delivered_at = datetime(2026, 11, 3, 8, 10, tzinfo=IST)
    db_session.add_all(
        [
            OrderLine(
                order_id=box.id,
                product_id=uuid.uuid4(),
                kind="box",
                qty=2,
                step_qty=500,
                step_unit="g",
                unit_price_paise=2_000,
                shortage_qty=1,
            ),
            OrderLine(
                order_id=box.id,
                product_id=uuid.uuid4(),
                kind="box",
                qty=1,
                step_qty=1,
                step_unit="piece",
                unit_price_paise=5_000,
            ),
            OrderEvent(order_id=box.id, to_status="dispatched", actor="rider"),
            OrderEvent(order_id=box.id, to_status="delivered", actor="captain", at=delivered_at),
            _issue(box, status="credited", credit_paise=1_500, resolved_by="auto"),
            _issue(box, status="open", open_reason="triage"),
            OrderEvent(order_id=refunded.id, to_status="dispatched", actor="rider"),
            OrderEvent(order_id=refunded.id, to_status="dispatched", actor="rider"),
            OrderEvent(
                order_id=refunded.id,
                to_status="refunded",
                actor="system",
                payload={"refund_paise": 40_000, "reason": "undelivered"},
            ),
            CarryOver(
                from_order_id=refunded.id,
                household_id=refunded.household_id,
                hub_id=HUB,
                society_id=refunded.society_id,
                product_id=uuid.uuid4(),
                steps=1,
                step_qty=1,
                step_unit="pack",
                paid_paise=9_000,
                missed_on=TUESDAY,
                expires_on=TUESDAY + timedelta(days=14),
                status="refunded",
            ),
        ]
    )
    await db_session.flush()

    facts = await orders_api.order_facts(
        db_session, date_from=TUESDAY, date_to=TUESDAY, hub_ids=[HUB]
    )

    by_id = {f.order_id: f for f in facts}
    assert set(by_id) == {box.id, refunded.id}
    first = by_id[box.id]
    assert (first.lines, first.short_lines, first.attempts) == (2, 1, 1)
    assert (first.credited_paise, first.refunded_paise, first.fees_paise) == (1_500, 0, 3_000)
    assert first.delivered_at == delivered_at
    assert first.slot_end == datetime(2026, 11, 3, 9, 0, tzinfo=IST)
    second = by_id[refunded.id]
    assert (second.refunded_paise, second.credited_paise, second.attempts) == (49_000, 0, 2)
    assert (second.lines, second.delivered_at, second.data_source) == (0, None, "synthetic")

    issues = await orders_api.issue_facts(db_session, date_from=TUESDAY, date_to=TUESDAY)
    assert sorted((i.status, i.credit_paise, i.resolved_by) for i in issues) == [
        ("credited", 1_500, "auto"),
        ("open", 0, None),
    ]
    assert {i.order_id for i in issues} == {box.id}
    assert (
        await orders_api.issue_facts(
            db_session, date_from=TUESDAY, date_to=TUESDAY, hub_ids=[uuid.uuid4()]
        )
        == []
    )
    assert await orders_api.first_delivery_date(db_session) == TUESDAY
