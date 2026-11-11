"""Revenue recognition on delivery (M5 §3.9): `orders.delivered` moves what the order still holds
in unearned_revenue to sales_revenue, once; a refund after that comes out of sales_revenue."""

import asyncio
import os
import uuid
from datetime import UTC, date, datetime
from typing import Any

import pytest
from alembic import command
from sqlalchemy import func, make_url, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.billing import jobs, revenue, service, wallet
from gharbasket.modules.billing.models import JournalEntry, LedgerAccount, Posting
from gharbasket.platform.db import create_engine
from gharbasket.platform.errors import ProblemError
from gharbasket.platform.jobs import run_job
from gharbasket.platform.models import OutboxEvent

from ..conftest import LOCAL_TEST_DATABASE_URL, alembic_config, make_settings, run_on_server
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


async def test_a_carried_item_is_recognised_on_its_missed_order(
    db_session: AsyncSession,
) -> None:
    """A missed ₹300 box: ₹60 of perishables refunded less the ₹20 fee (₹40), ₹180 and ₹60 of
    kept items carried. The ₹180 item rides on the next box and is recognised on the missed
    order; the ₹60 one expires and is refunded from what the missed order still holds."""
    missed = await paid(db_session, 30_000)
    user = await owner(db_session, missed)
    await wallet.refund_to_wallet(db_session, user, missed, 4_000, "second_miss")  # holds 26,000
    product, other = uuid.uuid4(), uuid.uuid4()
    box = await paid(db_session, 50_000, user)
    carried = [
        {
            "carry_id": str(uuid.uuid4()),
            "from_order_id": str(missed),
            "product_id": str(product),
            "paid_paise": 18_000,
        },
        {"from_order_id": "not an id", "product_id": str(other), "paid_paise": 1},
    ]

    await jobs.on_order_delivered(db_session, delivered(box, carried=carried))
    await jobs.on_order_delivered(db_session, delivered(box, carried=carried))

    assert (await legs(db_session, f"revenue:{box}"))["sales_revenue"] == -50_000
    assert await legs(db_session, f"revenue:{missed}:carry_{product.hex}") == {
        "unearned_revenue": 18_000,
        "sales_revenue": -18_000,
    }
    assert await revenue.held_paise(db_session, missed) == 8_000  # ₹60 kept, ₹20 fee
    await wallet.refund_to_wallet(db_session, user, missed, 6_000, f"carry_{other.hex}")
    assert await legs(db_session, f"refund:{missed}:carry_{other.hex}") == {
        "unearned_revenue": 6_000,
        "wallet": -6_000,
    }
    assert await revenue.held_paise(db_session, missed) == 2_000  # the fee stays (§3.9)


async def test_a_refund_takes_unearned_money_first_then_what_was_recognised_and_no_more(
    db_session: AsyncSession,
) -> None:
    """A kept item lost after another was delivered: the missed order still holds ₹40 and had
    ₹180 recognised, so a ₹60 refund is ₹40 unearned and ₹20 from sales; past what is left of
    what was paid, a refund is refused and nothing is posted."""
    missed = await paid(db_session, 30_000)
    user = await owner(db_session, missed)
    await wallet.refund_to_wallet(db_session, user, missed, 8_000, "second_miss")
    await revenue.recognise(db_session, missed, f"revenue:{missed}:carry_a", most_paise=18_000)
    assert await revenue.held_paise(db_session, missed) == 4_000

    await wallet.refund_to_wallet(db_session, user, missed, 6_000, "carry_b")

    assert await legs(db_session, f"refund:{missed}:carry_b") == {
        "unearned_revenue": 4_000,
        "sales_revenue": 2_000,
        "wallet": -6_000,
    }
    assert await revenue.sold_paise(db_session, missed) == 16_000
    never = uuid.uuid4()  # an order that was never paid
    for order, amount, reason in ((missed, 16_001, "issue_too_much"), (never, 100, "issue_x")):
        with pytest.raises(ProblemError) as refused:
            await wallet.refund_to_wallet(db_session, user, order, amount, reason)
        assert refused.value.code == "billing.order_refund_exceeds_paid"
        assert await service.find_entry(db_session, f"refund:{order}:{reason}") is None
    assert refused.value.extra == {"order_id": str(never), "refundable_paise": 0}
    await wallet.refund_to_wallet(db_session, user, missed, 16_000, "issue_all_the_rest")
    assert await revenue.sold_paise(db_session, missed) == 0


async def test_held_paise_reads_each_order_for_m4(db_session: AsyncSession) -> None:
    order, other = await paid(db_session, 12_000), await paid(db_session, 7_000)
    await jobs.on_order_delivered(db_session, delivered(other))
    unknown = uuid.uuid4()

    found = await billing_api.held_paise(db_session, [order, other, unknown])

    assert found == {order: 12_000, other: 0, unknown: 0}
    assert await billing_api.held_paise(db_session, []) == {}


async def test_a_refund_and_the_recognition_at_once_never_take_the_same_money(
    database_url: str,
) -> None:
    """Two transactions on one order (spec §3.9): whichever takes the order's lock first, the
    other waits for it, so the order's unearned money ends at 0, never below."""
    engine = create_engine(database_url, pooled=False)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    user = uuid.uuid4()
    first, second = uuid.uuid4(), uuid.uuid4()
    try:
        async with sessions() as setup:  # committed, so both transactions see it
            await wallet.credit_topup(
                setup, user, 60_000, payment_ref=f"race_{user.hex}", topup_id=uuid.uuid4()
            )
            for order in (first, second):
                await wallet.debit_for_order(setup, user, order, 30_000, DAY)
            await setup.commit()

        async def refund(session: AsyncSession, order: uuid.UUID) -> object:
            return await wallet.refund_to_wallet(session, user, order, 4_000, "issue_race")

        async def recognise(session: AsyncSession, order: uuid.UUID) -> object:
            return await revenue.recognise(session, order, f"revenue:{order}")

        for order, ahead, behind in ((first, refund, recognise), (second, recognise, refund)):
            async with sessions() as one, sessions() as two:
                await ahead(one, order)  # holds the order's lock until it commits
                waiting = asyncio.create_task(behind(two, order))
                await asyncio.sleep(0.5)
                assert not waiting.done()
                await one.commit()
                await waiting
                await two.commit()

        async with sessions() as check:
            assert await revenue.held_paise(check, first) == 0
            assert await revenue.held_paise(check, second) == 0
            assert await legs(check, f"refund:{first}:issue_race") == {
                "unearned_revenue": 4_000,
                "wallet": -4_000,
            }
            assert (await legs(check, f"revenue:{first}"))["sales_revenue"] == -26_000
            assert await legs(check, f"refund:{second}:issue_race") == {
                "sales_revenue": 4_000,
                "wallet": -4_000,
            }
            assert await revenue.sold_paise(check, second) == 26_000
    finally:
        await engine.dispose()


def test_the_migration_refuses_to_downgrade_once_revenue_is_recognised() -> None:
    """Deleting `revenue` entries would leave refunds from sales_revenue below zero, so the
    downgrade refuses while there are any; without them it goes down and up again."""
    base = os.environ.get("TEST_DATABASE_URL", LOCAL_TEST_DATABASE_URL)
    name = f"{make_url(base).database}_revdown"
    url = make_url(base).set(database=name).render_as_string(hide_password=False)

    async def recognise_one() -> None:
        engine = create_engine(url, pooled=False)
        try:
            async with AsyncSession(engine) as session:
                user, order = uuid.uuid4(), uuid.uuid4()
                await wallet.credit_topup(
                    session, user, 5_000, payment_ref="pay_down", topup_id=uuid.uuid4()
                )
                await wallet.debit_for_order(session, user, order, 5_000, DAY)
                await revenue.recognise(session, order, f"revenue:{order}")
                await session.commit()
        finally:
            await engine.dispose()

    run_on_server(base, f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
    run_on_server(base, f"CREATE DATABASE {name}")
    try:
        config = alembic_config(url)
        command.upgrade(config, "head")
        command.downgrade(config, "8b2e5d4c7a19")  # nothing recognised yet: it goes down
        command.upgrade(config, "head")
        asyncio.run(recognise_one())
        with pytest.raises(RuntimeError, match="cannot downgrade 5d1e9b3a7c42"):
            command.downgrade(config, "8b2e5d4c7a19")
    finally:
        run_on_server(base, f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")


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
