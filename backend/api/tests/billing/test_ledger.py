import uuid
from datetime import date

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import service, wallet
from gharbasket.modules.billing.models import JournalEntry, Posting
from gharbasket.modules.billing.service import Leg
from gharbasket.platform.errors import ProblemError

pytestmark = [pytest.mark.anyio, pytest.mark.services]


async def accounts(session: AsyncSession) -> tuple:
    clearing = await service.platform_account(session, "razorpay_clearing")
    unearned = await service.platform_account(session, "unearned_revenue")
    owner = uuid.uuid4()
    mine = await service.owned_account(session, "wallet", "user", owner, kind="liability")
    return clearing, unearned, mine


async def test_an_entry_must_balance_before_anything_is_written(db_session: AsyncSession) -> None:
    clearing, _, mine = await accounts(db_session)

    for legs in (
        [Leg(clearing, 100), Leg(mine, -99)],
        [Leg(clearing, 100)],
        [Leg(clearing, 0), Leg(mine, 0)],
        [Leg(mine, 100), Leg(mine, -100)],
    ):
        with pytest.raises(ValueError):
            await service.post(db_session, kind="adjustment", key="bad", legs=legs)

    assert await service.find_entry(db_session, "bad") is None


async def test_the_same_cause_posts_once_and_a_different_amount_is_a_conflict(
    db_session: AsyncSession,
) -> None:
    clearing, _, mine = await accounts(db_session)
    legs = [Leg(clearing, 5000), Leg(mine, -5000)]

    first, created = await service.post(db_session, kind="topup", key="topup:pay_1", legs=legs)
    again, created_again = await service.post(
        db_session, kind="topup", key="topup:pay_1", legs=legs
    )

    assert created and not created_again and first.id == again.id
    assert await service.balance(db_session, mine) == 5000  # a liability: what we owe
    assert await service.balance(db_session, clearing) >= 5000  # an asset
    with pytest.raises(ProblemError) as conflict:
        await service.post(
            db_session,
            kind="topup",
            key="topup:pay_1",
            legs=[Leg(clearing, 6000), Leg(mine, -6000)],
        )
    assert conflict.value.code == "billing.entry_conflict"
    assert conflict.value.status == 409


async def test_the_database_refuses_unbalanced_or_changed_entries(
    db_session: AsyncSession,
) -> None:
    clearing, _, mine = await accounts(db_session)
    entry, _ = await service.post(
        db_session, kind="topup", key="topup:pay_2", legs=[Leg(clearing, 700), Leg(mine, -700)]
    )
    await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))  # a balanced entry passes
    # Back to checking at commit: an entry is written before its postings.
    await db_session.execute(text("SET CONSTRAINTS ALL DEFERRED"))

    with pytest.raises(DBAPIError, match="append-only"):
        async with db_session.begin_nested():
            await db_session.execute(
                text("UPDATE billing.postings SET amount = 1 WHERE entry_id = :e"), {"e": entry.id}
            )
    with pytest.raises(DBAPIError, match="append-only"):
        async with db_session.begin_nested():
            await db_session.execute(
                text("DELETE FROM billing.journal_entries WHERE id = :e"), {"e": entry.id}
            )
    with pytest.raises(DBAPIError, match="does not sum to zero"):
        async with db_session.begin_nested():
            lonely = JournalEntry(kind="adjustment", idempotency_key="lonely")
            db_session.add(lonely)
            await db_session.flush()
            db_session.add_all(
                [
                    Posting(entry_id=lonely.id, account_id=clearing.id, amount=10),
                    Posting(entry_id=lonely.id, account_id=mine.id, amount=-9),
                ]
            )
            await db_session.flush()
            await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    with pytest.raises(DBAPIError, match="at least two postings"):
        async with db_session.begin_nested():
            db_session.add(JournalEntry(kind="adjustment", idempotency_key="empty"))
            await db_session.flush()
            await db_session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))


async def test_wallet_moves_money_once_per_cause_within_its_limits(
    db_session: AsyncSession,
) -> None:
    user, order = uuid.uuid4(), uuid.uuid4()
    day = date(2026, 10, 12)

    await wallet.credit_topup(db_session, user, 50_000, payment_ref="pay_a", topup_id=uuid.uuid4())
    await wallet.debit_for_order(db_session, user, order, 45_000, day)
    await wallet.debit_for_order(db_session, user, order, 45_000, day)  # a retried cut-off
    await wallet.refund_to_wallet(db_session, user, order, 3_000, "shortage")
    await wallet.refund_to_wallet(db_session, user, order, 3_000, "shortage")

    assert await wallet.balance(db_session, user) == 50_000 - 45_000 + 3_000
    with pytest.raises(wallet.InsufficientBalance) as short:
        await wallet.debit_for_order(db_session, user, uuid.uuid4(), 9_000, day)
    assert short.value.extra == {"balance_paise": 8_000, "needed_paise": 9_000}
    with pytest.raises(ProblemError) as full:
        await wallet.credit_topup(
            db_session, user, 995_000, payment_ref="pay_b", topup_id=uuid.uuid4()
        )
    assert full.value.code == "billing.wallet_limit"
    assert full.value.extra == {"room_paise": 992_000}
    with pytest.raises(ProblemError) as refund_full:
        await wallet.refund_to_wallet(db_session, user, order, 995_000, "undelivered")
    assert refund_full.value.code == "billing.wallet_limit"
    with pytest.raises(ValueError):
        await wallet.refund_to_wallet(db_session, user, order, 100, "Not a code!")
    with pytest.raises(ValueError):
        await wallet.debit_for_order(db_session, user, order, 0, day)


async def test_a_replayed_debit_is_not_refused_after_the_balance_changed(
    db_session: AsyncSession,
) -> None:
    user, order = uuid.uuid4(), uuid.uuid4()
    await wallet.credit_topup(db_session, user, 10_000, payment_ref="pay_c", topup_id=uuid.uuid4())
    first = await wallet.debit_for_order(db_session, user, order, 10_000, date(2026, 10, 12))

    again = await wallet.debit_for_order(db_session, user, order, 10_000, date(2026, 10, 12))

    assert again.id == first.id
    entries = (
        await db_session.execute(
            select(JournalEntry).where(JournalEntry.idempotency_key == f"box:{order}")
        )
    ).scalars()
    assert len(list(entries)) == 1


async def test_balances_read_many_wallets_in_one_query_like_balance(
    db_session: AsyncSession,
) -> None:
    rich, poor, none = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await wallet.credit_topup(db_session, rich, 50_000, payment_ref="pb_a", topup_id=uuid.uuid4())
    await wallet.credit_topup(db_session, poor, 9_000, payment_ref="pb_b", topup_id=uuid.uuid4())
    await wallet.debit_for_order(db_session, rich, uuid.uuid4(), 12_000, date(2026, 10, 12))

    got = await wallet.balances(db_session, [rich, poor, none, rich])

    assert got == {rich: 38_000, poor: 9_000, none: 0}
    assert got[rich] == await wallet.balance(db_session, rich)
    assert await wallet.balances(db_session, []) == {}
