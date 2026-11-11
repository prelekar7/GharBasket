"""Property tests of the ledger invariants (M5 §4.1, §9; handbook 05 §5.7.3).

Hypothesis generates sequences of wallet movements (top-ups, box debits, refunds, including
replays of an earlier cause with the same or a different amount) and deliveries, which recognise an
order's unearned money as revenue (§3.9). Each sequence runs in its own
transaction, which is rolled back, against a simple model of what should happen. After every
sequence:
1. every journal entry sums to zero per currency (the deferred trigger, forced to check now);
2. the wallet never goes below zero or above the ₹10,000 cap, and equals the model;
3. a replayed cause never creates a second entry;
4. the balance is the sum of the postings (the only way it is computed) and matches the model;
5. no order holds less than nothing in unearned_revenue, a delivered order holds nothing there
   (a refund after delivery comes out of sales_revenue), and the two hold what was debited less
   what was refunded.
"""

import asyncio
import uuid
from datetime import date

import pytest
from hypothesis import event, given, settings
from hypothesis import strategies as st
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import revenue, service, wallet
from gharbasket.modules.billing.models import JournalEntry, Posting
from gharbasket.platform.db import create_engine
from gharbasket.platform.errors import ProblemError

pytestmark = pytest.mark.services

CAP = wallet.CAP_PAISE
# Large amounts are common, so the cap and the balance are reached often (Hypothesis would
# otherwise mostly try small numbers).
amounts = st.one_of(
    st.integers(min_value=1, max_value=600_000),
    st.sampled_from([100_000, 400_000, 600_000, 999_999, 1_000_000]),
)
movement = st.one_of(
    st.tuples(st.just("topup"), amounts, st.integers(0, 3)),
    st.tuples(st.just("debit"), amounts, st.integers(0, 3)),
    st.tuples(st.just("refund"), amounts, st.integers(0, 3)),
    st.tuples(st.just("deliver"), st.just(0), st.integers(0, 3)),
)
ORDERS = [uuid.UUID(int=n + 1) for n in range(4)]


async def run(database_url: str, moves: list[tuple[str, int, int]]) -> None:
    engine = create_engine(database_url, pooled=False)
    user = uuid.uuid4()
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            session = AsyncSession(bind=connection, expire_on_commit=False)
            try:
                await check(session, user, moves)
            finally:
                await session.close()
                await transaction.rollback()
    finally:
        await engine.dispose()


async def check(session: AsyncSession, user: uuid.UUID, moves: list[tuple[str, int, int]]) -> None:
    model = 0
    posted: dict[str, int] = {}  # cause → amount, as the model expects it recorded
    delivered: set[int] = set()
    for kind, amount, n in moves:
        if kind == "deliver":
            # Only a paid box is delivered, so a later debit of it can only be a replay.
            if f"box:{ORDERS[n]}" in posted:
                await revenue.recognise(session, ORDERS[n], f"revenue:{ORDERS[n]}")
                delivered.add(n)
                event("deliver")
            continue
        key = {"topup": f"topup:pay_{user}_{n}", "debit": f"box:{ORDERS[n]}"}.get(
            kind, f"refund:{ORDERS[n]}:shortage"
        )
        replay = key in posted
        expected_error = None
        if replay and posted[key] != amount:
            expected_error = "billing.entry_conflict"
        elif not replay and kind == "debit" and model < amount:
            expected_error = "billing.insufficient_balance"
        elif not replay and kind != "debit" and model + amount > CAP:
            expected_error = "billing.wallet_limit"
        event(expected_error or ("replay" if replay else kind))
        try:
            if kind == "topup":
                await wallet.credit_topup(
                    session, user, amount, payment_ref=f"pay_{user}_{n}", topup_id=ORDERS[n]
                )
            elif kind == "debit":
                await wallet.debit_for_order(session, user, ORDERS[n], amount, date(2026, 10, 6))
            else:
                await wallet.refund_to_wallet(session, user, ORDERS[n], amount, "shortage")
        except ProblemError as problem:
            assert problem.code == expected_error, (kind, amount, n, model)
            continue
        assert expected_error is None, (kind, amount, n, model)
        if not replay:
            posted[key] = amount
            model += -amount if kind == "debit" else amount

        current = await wallet.balance(session, user)
        assert current == model
        assert 0 <= current <= CAP

    await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))  # every entry balanced
    keys = list(posted)
    if keys:
        count = (
            await session.execute(
                select(func.count()).where(JournalEntry.idempotency_key.in_(keys))
            )
        ).scalar_one()
        assert count == len(keys)
    for n, order in enumerate(ORDERS):
        held = await revenue.held_paise(session, order)
        assert held >= 0
        if n in delivered:
            assert held == 0
    unearned = await service.platform_account(session, "unearned_revenue")
    sales = await service.platform_account(session, "sales_revenue")
    kept = await session.scalar(
        select(func.coalesce(func.sum(-Posting.amount), 0))
        .join(JournalEntry, JournalEntry.id == Posting.entry_id)
        .where(
            Posting.account_id.in_([unearned.id, sales.id]),
            JournalEntry.ref_type == "order",
            JournalEntry.ref_id.in_(ORDERS),
        )
    )
    debited = sum(v for k, v in posted.items() if k.startswith("box:"))
    refunded = sum(v for k, v in posted.items() if k.startswith("refund:"))
    assert int(kept or 0) == debited - refunded


@settings(max_examples=120, deadline=None)
@given(moves=st.lists(movement, max_size=14))
def test_ledger_invariants_hold_for_any_sequence_of_movements(
    database_url: str, moves: list[tuple[str, int, int]]
) -> None:
    asyncio.run(run(database_url, moves))
