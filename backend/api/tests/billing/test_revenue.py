"""Revenue recognition on delivery (M5 §3.9): `orders.delivered` moves what the order still holds
in unearned_revenue to sales_revenue, once; a refund after that comes out of sales_revenue."""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import jobs, revenue, service, wallet
from gharbasket.modules.billing.models import JournalEntry, LedgerAccount, Posting
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.jobs import run_job
from gharbasket.platform.models import OutboxEvent

from ..conftest import make_settings
from .paid import paid_topup

pytestmark = [pytest.mark.anyio, pytest.mark.services]

DAY = date(2026, 11, 10)
NOW = datetime(2026, 11, 11, 6, 0, tzinfo=UTC)


async def paid(db: AsyncSession, amount: int, user: uuid.UUID | None = None) -> uuid.UUID:
    """A box of `amount` paid from a fresh wallet; returns the order id."""
    user = user or uuid.uuid4()
    await paid_topup(db, user, amount)
    order = uuid.uuid4()
    await wallet.debit_for_order(db, user, order, amount, DAY)
    return order


async def owner(db: AsyncSession, order: uuid.UUID) -> uuid.UUID:
    """The user whose wallet paid for the order."""
    found = await db.scalar(
        select(LedgerAccount.owner_id)
        .join(Posting, Posting.account_id == LedgerAccount.id)
        .join(JournalEntry, JournalEntry.id == Posting.entry_id)
        .where(JournalEntry.idempotency_key == f"box:{order}")
    )
    assert found is not None
    return found


def delivered(order: uuid.UUID, **extra: Any) -> OutboxEvent:
    payload = {"order_id": str(order), "delivery_date": DAY.isoformat(), "kind": "box", **extra}
    return OutboxEvent(id=uuid.uuid4(), event_type=jobs.ORDER_DELIVERED, payload=payload)


async def legs(db: AsyncSession, key: str) -> dict[str, int]:
    """An entry's postings by account code (debit +)."""
    rows = await db.execute(
        select(LedgerAccount.code, Posting.amount)
        .join(Posting, Posting.account_id == LedgerAccount.id)
        .join(JournalEntry, JournalEntry.id == Posting.entry_id)
        .where(JournalEntry.idempotency_key == key)
    )
    return {code: int(amount) for code, amount in rows.all()}


async def revenue_entries(db: AsyncSession, order: uuid.UUID) -> int:
    return int(
        await db.scalar(
            select(func.count()).where(JournalEntry.kind == "revenue", JournalEntry.ref_id == order)
        )
        or 0
    )


async def test_a_delivered_order_recognises_what_it_still_holds_once(
    db_session: AsyncSession,
) -> None:
    order = await paid(db_session, 45_000)
    await wallet.refund_to_wallet(
        db_session, await owner(db_session, order), order, 3_000, "shortage"
    )

    await jobs.on_order_delivered(db_session, delivered(order))
    await jobs.on_order_delivered(db_session, delivered(order))  # the event again

    assert await legs(db_session, f"revenue:{order}") == {
        "unearned_revenue": 42_000,
        "sales_revenue": -42_000,
    }
    entry = await service.find_entry(db_session, f"revenue:{order}")
    assert entry is not None and (entry.ref_type, entry.ref_id) == ("order", order)
    assert entry.details == {"delivery_date": DAY.isoformat(), "kind": "box"}
    assert await revenue_entries(db_session, order) == 1
    assert await revenue.held_paise(db_session, order) == 0


async def test_an_order_wholly_refunded_or_never_paid_recognises_nothing(
    db_session: AsyncSession,
) -> None:
    order = await paid(db_session, 20_000)
    await wallet.refund_to_wallet(
        db_session, await owner(db_session, order), order, 20_000, "undelivered"
    )
    never = uuid.uuid4()

    await jobs.on_order_delivered(db_session, delivered(order))
    await jobs.on_order_delivered(db_session, delivered(never))

    assert await revenue_entries(db_session, order) == 0
    assert await revenue_entries(db_session, never) == 0


async def test_a_refund_after_delivery_comes_out_of_sales_revenue(
    db_session: AsyncSession,
) -> None:
    order = await paid(db_session, 45_000)
    user = await owner(db_session, order)
    await jobs.on_order_delivered(db_session, delivered(order))

    issue = await wallet.refund_to_wallet(db_session, user, order, 5_000, "issue_abc")
    again = await wallet.refund_to_wallet(db_session, user, order, 5_000, "issue_abc")

    assert again.id == issue.id
    assert await legs(db_session, "refund:" + f"{order}:issue_abc") == {
        "sales_revenue": 5_000,
        "wallet": -5_000,
    }
    assert await revenue.held_paise(db_session, order) == 0
    assert await wallet.balance(db_session, user) == 5_000
    with pytest.raises(ProblemError) as conflict:
        await wallet.refund_to_wallet(db_session, user, order, 6_000, "issue_abc")
    assert conflict.value.code == "billing.entry_conflict"


async def test_a_refund_before_the_consumer_runs_is_unearned_and_the_rest_is_recognised(
    db_session: AsyncSession,
) -> None:
    order = await paid(db_session, 30_000)
    await wallet.refund_to_wallet(
        db_session, await owner(db_session, order), order, 4_000, "issue_x"
    )

    await jobs.on_order_delivered(db_session, delivered(order))

    assert await legs(db_session, f"refund:{order}:issue_x") == {
        "unearned_revenue": 4_000,
        "wallet": -4_000,
    }
    assert (await legs(db_session, f"revenue:{order}"))["sales_revenue"] == -26_000


async def test_a_carried_item_is_recognised_on_its_missed_order_and_a_later_refund_splits(
    db_session: AsyncSession,
) -> None:
    missed = await paid(db_session, 30_000)
    user = await owner(db_session, missed)
    await wallet.refund_to_wallet(db_session, user, missed, 8_000, "second_miss")  # holds 22,000
    product, other = uuid.uuid4(), uuid.uuid4()
    box = await paid(db_session, 50_000, user)
    carried = [
        {
            "carry_id": str(uuid.uuid4()),
            "from_order_id": str(missed),
            "product_id": str(product),
            "paid_paise": 18_000,
        },
        {"carry_id": "x", "from_order_id": "not an id", "product_id": str(other), "paid_paise": 1},
    ]

    await jobs.on_order_delivered(db_session, delivered(box, carried=carried))
    await jobs.on_order_delivered(db_session, delivered(box, carried=carried))

    assert (await legs(db_session, f"revenue:{box}"))["sales_revenue"] == -50_000
    assert await legs(db_session, f"revenue:{missed}:carry_{product.hex}") == {
        "unearned_revenue": 18_000,
        "sales_revenue": -18_000,
    }
    assert await revenue.held_paise(db_session, missed) == 4_000  # the rest of the missed box
    # A refund of more than the missed order still holds: unearned first, then sales.
    await wallet.refund_to_wallet(db_session, user, missed, 6_000, f"carry_{other.hex}")
    assert await legs(db_session, f"refund:{missed}:carry_{other.hex}") == {
        "unearned_revenue": 4_000,
        "sales_revenue": 2_000,
        "wallet": -6_000,
    }
    assert await revenue.held_paise(db_session, missed) == 0


async def test_a_payload_without_an_order_is_skipped_not_raised(db_session: AsyncSession) -> None:
    event = OutboxEvent(id=uuid.uuid4(), event_type=jobs.ORDER_DELIVERED, payload={"kind": "box"})
    await jobs.on_order_delivered(db_session, event)
    event.payload = ["not", "a", "dict"]  # pyright: ignore[reportAttributeAccessIssue]
    await jobs.on_order_delivered(db_session, event)


async def test_the_backfill_recognises_relayed_deliveries_once(db_session: AsyncSession) -> None:
    first, second = await paid(db_session, 10_000), await paid(db_session, 12_000)
    pending = await paid(db_session, 9_000)  # not relayed yet: the consumer will see it
    sent = datetime.now(UTC)
    for order, at in ((first, sent), (second, sent), (pending, None)):
        event = delivered(order)
        event.sent_at = at
        db_session.add(event)
    await db_session.flush()

    runs = []
    for _ in range(2):
        run = await run_job(
            db_session, "recognise_delivered_revenue", settings=make_settings(), scheduled_for=NOW
        )
        assert run.status == "ok", run.error
        runs.append(run.summary)

    assert runs[0] == {"events": 2, "recognised": 2, "paise": 22_000}
    assert runs[1] == {"events": 2, "recognised": 0, "paise": 0}
    assert await revenue_entries(db_session, pending) == 0


async def test_the_probe_reports_a_delivered_order_still_holding_unearned_money(
    db_session: AsyncSession,
) -> None:
    order = await paid(db_session, 15_000)
    event = delivered(order)
    event.created_at = event.sent_at = NOW - timedelta(hours=2)
    db_session.add(event)
    await db_session.flush()

    assert await jobs.revenue_unrecognised(db_session, NOW - timedelta(hours=1, minutes=30)) == []
    assert await jobs.revenue_unrecognised(db_session, NOW) == [
        "1 delivered orders still hold unearned revenue; run recognise_delivered_revenue"
    ]
    assert await jobs.revenue_unrecognised(db_session, NOW + timedelta(days=8)) == []
    await jobs.on_order_delivered(db_session, event)
    assert await jobs.revenue_unrecognised(db_session, NOW) == []
