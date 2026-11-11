"""Property tests of the ledger invariants (M5 §4.1, §9; handbook 05 §5.7.3).

Hypothesis generates sequences of wallet movements (top-ups, box debits, order refunds for two
reasons, including replays of an earlier cause with the same or a different amount) and
deliveries, which recognise an order's unearned money as revenue (§3.9). Each sequence runs in
its own transaction, which is rolled back, against a simple model of what should happen: a refund
of more than is left of what its order paid (an order never debited included) is refused. After
every sequence:
1. every journal entry sums to zero per currency (the deferred trigger, forced to check now);
2. the wallet never goes below zero or above the ₹10,000 cap, and equals the model;
3. a replayed cause never creates a second entry;
4. the balance is the sum of the postings (the only way it is computed) and matches the model;
5. per order, neither unearned_revenue nor net sales_revenue is below zero, a delivered order
   holds nothing unearned, an order with no `revenue` entry has no sales_revenue posting, and
   the two accounts hold what was debited less what was refunded.
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
# otherwise mostly try small numbers); refunds also try amounts that fit inside a box.
amounts = st.one_of(
    st.integers(min_value=1, max_value=600_000),
    st.sampled_from([100_000, 400_000, 600_000, 999_999, 1_000_000]),
)
refunds = st.one_of(amounts, st.integers(min_value=1, max_value=50_000))
REASONS = ("shortage", "issue_a")
movement = st.one_of(
    st.tuples(st.just("topup"), amounts, st.integers(0, 3), st.just(0)),
    st.tuples(st.just("debit"), amounts, st.integers(0, 3), st.just(0)),
    st.tuples(st.just("refund"), refunds, st.integers(0, 3), st.integers(0, 1)),
    st.tuples(st.just("deliver"), st.just(0), st.integers(0, 3), st.just(0)),
)
ORDERS = [uuid.UUID(int=n + 1) for n in range(4)]
Move = tuple[str, int, int, int]


async def run(database_url: str, moves: list[Move]) -> None:
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


def expected(
    kind: str, amount: int, replay: bool, recorded: int | None, model: int, left: int
) -> str | None:
    """The error the model expects, in the order the code checks."""
    if replay:
        return "billing.entry_conflict" if recorded != amount else None
    if kind == "debit":
        return "billing.insufficient_balance" if model < amount else None
    if model + amount > CAP:
        return "billing.wallet_limit"  # no payment to send it back to, so it cannot go to source
    if kind == "refund" and amount > left:
        return "billing.order_refund_exceeds_paid"
    return None


async def check(session: AsyncSession, user: uuid.UUID, moves: list[Move]) -> None:
    model = 0
    posted: dict[str, int] = {}  # cause → amount, as the model expects it recorded
    left = [0] * len(ORDERS)  # what is left of what each order paid
    delivered: set[int] = set()
    for kind, amount, n, r in moves:
        order = ORDERS[n]
        if kind == "deliver":
            # Only a paid box is delivered, so a later debit of it can only be a replay.
            if f"box:{order}" in posted:
                await revenue.recognise(session, order, f"revenue:{order}")
                delivered.add(n)
                event("deliver")
            continue
        key = {"topup": f"topup:pay_{user}_{n}", "debit": f"box:{order}"}.get(
            kind, f"refund:{order}:{REASONS[r]}"
        )
        replay = key in posted
        error = expected(kind, amount, replay, posted.get(key), model, left[n])
        event(error or ("replay" if replay else kind))
        try:
            if kind == "topup":
                await wallet.credit_topup(
                    session, user, amount, payment_ref=f"pay_{user}_{n}", topup_id=order
                )
            elif kind == "debit":
                await wallet.debit_for_order(session, user, order, amount, date(2026, 10, 6))
            else:
                await wallet.refund_to_wallet(session, user, order, amount, REASONS[r])
        except ProblemError as problem:
            assert problem.code == error, (kind, amount, n, model)
            continue
        assert error is None, (kind, amount, n, model)
        if not replay:
            posted[key] = amount
            model += -amount if kind == "debit" else amount
            left[n] += amount if kind == "debit" else -amount if kind == "refund" else 0

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
    sales = await service.platform_account(session, "sales_revenue")
    for n, order in enumerate(ORDERS):
        held, sold = (
            await revenue.held_paise(session, order),
            await revenue.sold_paise(session, order),
        )
        assert held >= 0 and sold >= 0, (n, held, sold)
        assert held + sold == left[n], (n, held, sold, left[n])
        if n in delivered:
            assert held == 0
        recognised = await service.find_entry(session, f"revenue:{order}")
        if recognised is None:
            on_sales = await session.scalar(
                select(func.count())
                .select_from(Posting)
                .join(JournalEntry, JournalEntry.id == Posting.entry_id)
                .where(
                    Posting.account_id == sales.id,
                    JournalEntry.ref_type == "order",
                    JournalEntry.ref_id == order,
                )
            )
            assert on_sales == 0, n
    debited = sum(v for k, v in posted.items() if k.startswith("box:"))
    refunded = sum(v for k, v in posted.items() if k.startswith("refund:"))
    assert sum(left) == debited - refunded


@settings(max_examples=120, deadline=None)
@given(moves=st.lists(movement, max_size=14))
def test_ledger_invariants_hold_for_any_sequence_of_movements(
    database_url: str, moves: list[Move]
) -> None:
    asyncio.run(run(database_url, moves))
