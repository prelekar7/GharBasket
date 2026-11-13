"""`orders.closed` (M4 §3.8, M5 §3.9): a `refunded` order that has nothing left open publishes it
once, and M5 recognises what the order still holds (the ₹20 second-miss fee, the rest of a partial
refund) as `retained` revenue; `republish_closed_orders` backfills; the ops probe reports a closed
refunded order still holding money."""

import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import jobs as billing_jobs
from gharbasket.modules.billing import revenue as billing_revenue
from gharbasket.modules.billing.models import JournalEntry
from gharbasket.modules.logistics import deliveries
from gharbasket.modules.orders import api as orders_api
from gharbasket.modules.orders import jobs
from gharbasket.modules.orders.models import Order, OrderEvent
from gharbasket.platform.models import OutboxEvent

from ..identity.conftest import ClientFactory
from ..identity.fake_firebase import FakeGoogle
from .test_carry_over import (
    AT_THE_DOOR,
    KEPT_PAISE,
    LATER,
    LOCKED,
    MISSED,
    NEXT,
    at,
    events,
    household,
    job,
    place,
    rows_of,
)

pytestmark = [pytest.mark.anyio, pytest.mark.services, pytest.mark.usefixtures("world")]

FEE = deliveries.SECOND_MISS_FEE_PAISE
FRESH_ONLY = {"bhindi": (2, 2_000)}  # one perishable line: nothing to carry


async def second_miss(db: AsyncSession, order_id: uuid.UUID) -> None:
    """M7's second miss as `schedule_day` does it: the move does not close the order when rows
    follow, then `carry_over`."""
    order = await orders_api.get(db, order_id)
    amount, kept = await deliveries.second_miss_refund(db, order)
    await orders_api.transition(
        db,
        order_id,
        "refunded",
        actor="system",
        payload={"refund_paise": amount, "reason": "second_miss"},
        closes=not kept,
    )
    if kept:
        await orders_api.carry_over(db, order_id, kept)
    await db.commit()


async def closed_for(db: AsyncSession, *orders: uuid.UUID) -> list[str]:
    wanted = {str(o) for o in orders}
    return [p["order_id"] for p in await events(db, "orders.closed") if p["order_id"] in wanted]


async def consume(db: AsyncSession, *, closed_first: bool = False) -> None:
    """M5's consumers over every event in the outbox, as the relay would, oldest first (or, with
    `closed_first`, every `orders.closed` before any `orders.delivered`)."""
    handlers = {
        billing_jobs.ORDER_DELIVERED: billing_jobs.on_order_delivered,
        billing_jobs.ORDER_CLOSED: billing_jobs.on_order_closed,
    }
    found = await db.scalars(
        select(OutboxEvent)
        .where(OutboxEvent.event_type.in_(list(handlers)))
        .order_by(OutboxEvent.id)
    )
    batch = list(found)
    if closed_first:
        batch.sort(key=lambda e: e.event_type != billing_jobs.ORDER_CLOSED)
    for event in batch:
        await handlers[event.event_type](db, event)
    await db.commit()


async def deliver(db: AsyncSession, order_id: uuid.UUID) -> None:
    for to in ("packed", "dispatched", "at_society", "delivered"):
        await orders_api.transition(db, order_id, to, actor="hub")
    await db.commit()


async def retained(db: AsyncSession, order_id: uuid.UUID) -> JournalEntry | None:
    return await db.scalar(
        select(JournalEntry).where(JournalEntry.idempotency_key == f"revenue:{order_id}")
    )


async def test_the_fee_of_a_box_with_nothing_to_carry_is_recognised_at_the_move(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    _, _, owner = await household(clients, google)
    missed = await place(db_session, owner, MISSED, AT_THE_DOOR, box=FRESH_ONLY)

    await second_miss(db_session, missed)

    assert await closed_for(db_session, missed) == [str(missed)]  # at the move, once
    [event] = await events(db_session, "orders.closed")
    assert set(event) == {"order_id", "household_id", "hub_id"}
    assert (
        await billing_revenue.held_paise(db_session, missed) == FEE
    )  # 4,000 paid, 2,000 back, the fee kept

    await consume(db_session)
    await consume(db_session)  # the events again: nothing more

    entry = await retained(db_session, missed)
    assert entry is not None and entry.kind == "revenue"
    assert entry.details == {"reason": "retained"}
    assert (entry.ref_type, entry.ref_id) == ("order", missed)
    assert await billing_revenue.held_paise(db_session, missed) == 0
    assert await billing_revenue.sold_paise(db_session, missed) == FEE
    assert await closed_for(db_session, missed) == [str(missed)]


async def test_the_rest_of_a_partial_hub_refund_is_recognised(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    _, _, owner = await household(clients, google)
    missed = await place(db_session, owner, MISSED, AT_THE_DOOR)
    total = (await orders_api.get(db_session, missed)).total_paise
    await orders_api.transition(
        db_session,
        missed,
        "refunded",
        actor="system",
        payload={"refund_paise": 10_000, "reason": "triage"},
    )
    await db_session.commit()

    await consume(db_session)

    assert await closed_for(db_session, missed) == [str(missed)]
    assert await billing_revenue.sold_paise(db_session, missed) == total - 10_000
    assert await billing_revenue.held_paise(db_session, missed) == 0


async def test_an_order_refunded_in_full_closes_and_posts_nothing(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    _, _, owner = await household(clients, google)
    missed = await place(db_session, owner, MISSED, AT_THE_DOOR)
    await orders_api.transition(
        db_session, missed, "refunded", actor="system", payload={"reason": "triage"}
    )
    await db_session.commit()

    await consume(db_session)

    assert await closed_for(db_session, missed) == [str(missed)]
    assert await retained(db_session, missed) is None
    assert await billing_revenue.held_paise(db_session, missed) == 0


async def test_carried_rows_keep_the_order_open_until_the_last_one_is_delivered(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    _, _, owner = await household(clients, google)
    missed = await place(db_session, owner, MISSED, AT_THE_DOOR)
    next_box = await place(db_session, owner, NEXT, LOCKED)

    await second_miss(db_session, missed)
    assert await closed_for(db_session, missed) == []  # rows follow: not closed at the move

    for to in ("packed", "dispatched", "at_society"):
        await orders_api.transition(db_session, next_box, to, actor="hub")
    assert await closed_for(db_session, missed) == []  # attached and packed rows are open
    await orders_api.transition(db_session, next_box, "delivered", actor="hub")
    await db_session.commit()

    assert await closed_for(db_session, missed) == [str(missed)]  # the last row, once
    await consume(db_session)  # delivered first (the carried items), then closed (the fee)
    total = (await orders_api.get(db_session, missed)).total_paise
    assert await billing_revenue.held_paise(db_session, missed) == 0
    assert await billing_revenue.sold_paise(db_session, missed) == total - FEE
    entry = await retained(db_session, missed)
    assert entry is not None and entry.details == {"reason": "retained"}
    assert await billing_revenue.sold_paise(db_session, next_box) == (
        (await orders_api.get(db_session, next_box)).total_paise
    )


async def test_rows_that_come_back_to_pending_keep_the_order_open(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    _, _, owner = await household(clients, google)
    missed = await place(db_session, owner, MISSED, AT_THE_DOOR)
    next_box = await place(db_session, owner, NEXT, (*AT_THE_DOOR[:-1], "undelivered"))
    await second_miss(db_session, missed)
    await orders_api.transition(
        db_session, next_box, "refunded", actor="system", payload={"reason": "second_miss"}
    )
    await db_session.commit()

    assert all(r.status == "pending" for r in (await rows_of(db_session, missed)).values())
    assert await closed_for(db_session, missed) == []
    assert await closed_for(db_session, next_box) == [str(next_box)]


async def test_the_expiry_and_a_lost_item_close_the_order_with_the_last_row_only(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    _, _, owner = await household(clients, google)
    missed = await place(db_session, owner, MISSED, AT_THE_DOOR)
    await second_miss(db_session, missed)
    rows = await rows_of(db_session, missed)

    await orders_api.refund_carry(db_session, rows["moong-dal"].id, None)
    await db_session.commit()
    assert await closed_for(db_session, missed) == []  # the toor dal is still open

    await job(db_session, "expire_carry_overs", at(LATER + timedelta(days=1), 2, 40))
    assert await closed_for(db_session, missed) == [str(missed)]
    await job(db_session, "expire_carry_overs", at(LATER + timedelta(days=2), 2, 40))
    await orders_api.refund_carry(db_session, rows["moong-dal"].id, None)  # already refunded
    await db_session.commit()
    assert await closed_for(db_session, missed) == [str(missed)]  # still once

    await consume(db_session)
    assert KEPT_PAISE > FEE
    assert await billing_revenue.sold_paise(db_session, missed) == FEE  # only the fee was kept
    assert await billing_revenue.held_paise(db_session, missed) == 0


async def test_the_backfill_republishes_only_closed_orders_that_still_hold_money(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    _, _, owner = await household(clients, google)
    closed = await place(db_session, owner, MISSED, AT_THE_DOOR, box=FRESH_ONLY)
    await second_miss(db_session, closed)
    whole = await place(db_session, owner, NEXT, AT_THE_DOOR, box=FRESH_ONLY)
    await orders_api.transition(
        db_session, whole, "refunded", actor="system", payload={"reason": "triage"}
    )
    still_open = await place(db_session, owner, LATER, AT_THE_DOOR)
    await second_miss(db_session, still_open)
    await db_session.commit()
    assert await closed_for(db_session, closed) == [str(closed)]  # published at the move

    summary = await job(db_session, "republish_closed_orders", datetime.now(UTC))

    assert summary == {"closed": 2, "published": 1}  # not the open one, nothing for the whole one
    assert await closed_for(db_session, closed) == [str(closed)] * 2
    assert await closed_for(db_session, whole, still_open) == [str(whole)]
    await consume(db_session)
    assert await billing_revenue.held_paise(db_session, closed) == 0
    assert await billing_revenue.held_paise(db_session, still_open) > 0  # open: nothing posted
    again = await job(db_session, "republish_closed_orders", datetime.now(UTC))
    assert again == {"closed": 2, "published": 0}  # nothing left holding money
    entries = await db_session.scalars(
        select(JournalEntry).where(JournalEntry.kind == "revenue", JournalEntry.ref_id == closed)
    )
    assert len(list(entries)) == 1


async def test_the_probe_reports_a_closed_refunded_order_still_holding_money(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    _, _, owner = await household(clients, google)
    closed = await place(db_session, owner, MISSED, AT_THE_DOOR, box=FRESH_ONLY)
    await second_miss(db_session, closed)
    still_open = await place(db_session, owner, NEXT, AT_THE_DOOR)
    await second_miss(db_session, still_open)  # holds money too, but its rows are open
    now = datetime.now(UTC)  # the moves are stamped by the database now

    assert await jobs.revenue_unrecognised(db_session, now + timedelta(hours=2)) == []  # < a day
    message = ["1 refunded order still holds unearned revenue; run republish_closed_orders"]
    assert await jobs.revenue_unrecognised(db_session, now + timedelta(days=2)) == message
    assert await jobs.revenue_unrecognised(db_session, now + timedelta(days=8)) == []  # too old
    await consume(db_session)
    assert await jobs.revenue_unrecognised(db_session, now + timedelta(days=2)) == []


async def test_closed_consumed_before_delivered_leaves_the_total_and_the_carry_entries_empty(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    """Events may reach M5 in either order: `orders.closed` first recognises everything the missed
    order holds, the carried items' entries then find 0, and the order's total is the same."""
    _, _, owner = await household(clients, google)
    missed = await place(db_session, owner, MISSED, AT_THE_DOOR)
    next_box = await place(db_session, owner, NEXT, LOCKED)
    await second_miss(db_session, missed)
    await deliver(db_session, next_box)
    total = (await orders_api.get(db_session, missed)).total_paise

    await consume(db_session, closed_first=True)

    assert await billing_revenue.sold_paise(db_session, missed) == total - FEE
    assert await billing_revenue.held_paise(db_session, missed) == 0
    keys = list(
        await db_session.scalars(
            select(JournalEntry.idempotency_key).where(
                JournalEntry.kind == "revenue", JournalEntry.ref_id == missed
            )
        )
    )
    assert keys == [f"revenue:{missed}"]  # the carried items found nothing left


async def test_no_control_key_reaches_the_timeline_or_the_events(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    _, _, owner = await household(clients, google)
    carried = await place(db_session, owner, MISSED, AT_THE_DOOR)
    plain = await place(db_session, owner, NEXT, AT_THE_DOOR, box=FRESH_ONLY)
    await second_miss(db_session, carried)  # closes=False
    await second_miss(db_session, plain)  # closes=True

    moves = await db_session.scalars(
        select(OrderEvent.payload).where(
            OrderEvent.order_id.in_([carried, plain]), OrderEvent.to_status == "refunded"
        )
    )
    for payload in moves:
        assert payload is not None and set(payload) == {"refund_paise", "reason"}
    published = await db_session.scalars(select(OutboxEvent.payload))
    for body in published:
        assert not {"closes", "carry_over"} & set(body)


async def test_a_box_delivered_with_rows_from_two_missed_orders_closes_both(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    _, _, owner = await household(clients, google)
    first = await place(db_session, owner, date(2026, 10, 6), AT_THE_DOOR)
    second = await place(db_session, owner, MISSED, AT_THE_DOOR)
    next_box = await place(db_session, owner, NEXT, LOCKED)
    await second_miss(db_session, first)
    await second_miss(db_session, second)
    assert await closed_for(db_session, first, second) == []

    await deliver(db_session, next_box)

    assert sorted(await closed_for(db_session, first, second)) == sorted([str(first), str(second)])
    await consume(db_session)
    for order in (first, second):
        assert await billing_revenue.held_paise(db_session, order) == 0


async def test_a_tiffin_refunded_in_full_posts_nothing_and_a_partial_one_is_retained(
    clients: ClientFactory, google: FakeGoogle, db_session: AsyncSession
) -> None:
    _, _, owner = await household(clients, google)
    whole = await place(db_session, owner, MISSED, LOCKED)
    partial = await place(db_session, owner, NEXT, LOCKED)
    await db_session.execute(
        update(Order).where(Order.id.in_([whole, partial])).values(kind="tiffin")
    )
    total = (await orders_api.get(db_session, partial)).total_paise
    await orders_api.transition(
        db_session, whole, "refunded", actor="system", payload={"reason": "no_kitchen"}
    )
    await orders_api.transition(
        db_session,
        partial,
        "refunded",
        actor="system",
        payload={"reason": "triage", "refund_paise": 1_000},
    )
    await db_session.commit()

    assert await closed_for(db_session, whole, partial) == [str(whole), str(partial)]
    await consume(db_session)

    assert await retained(db_session, whole) is None
    assert await billing_revenue.held_paise(db_session, whole) == 0
    assert await billing_revenue.sold_paise(db_session, partial) == total - 1_000
    entry = await retained(db_session, partial)
    assert entry is not None and entry.details == {"reason": "retained"}
