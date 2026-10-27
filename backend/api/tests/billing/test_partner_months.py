"""A partner's earned, paid-out and owed money per IST month (M5 §3.5, for M8 §3.7)."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.billing import service, statements
from gharbasket.modules.billing.models import JournalEntry, Posting
from gharbasket.platform.db import create_engine
from gharbasket.platform.errors import ProblemError

pytestmark = [pytest.mark.anyio, pytest.mark.services]

# "Now" for these tests: Wednesday 28 October 2026, 14:05 IST.
UNTIL = datetime(2026, 10, 28, 8, 35, tzinfo=UTC)


async def earn(
    session: AsyncSession,
    partner: uuid.UUID,
    basis: str,
    units: int,
    amount: int,
    worked: datetime,
    posted: datetime | None = None,
) -> None:
    """What `accrue_earning` posts, stamped `posted` (the ledger is append-only, so the clock
    cannot be set afterwards): fulfilment_expense to the partner's payable, with `earned_at`."""
    payable = await service.owned_account(
        session, "partner_payable", "partner", partner, kind="liability"
    )
    expense = await service.platform_account(session, "fulfilment_expense")
    entry = JournalEntry(
        kind="earning",
        idempotency_key=f"test:{uuid.uuid4()}",
        created_at=posted or worked,
        details={"basis": basis, "units": units, "earned_at": worked.isoformat()},
    )
    session.add(entry)
    await session.flush()
    session.add_all(
        [
            Posting(entry_id=entry.id, account_id=expense.id, amount=amount),
            Posting(entry_id=entry.id, account_id=payable.id, amount=-amount),
        ]
    )
    await session.flush()


async def settle(
    session: AsyncSession, partner: uuid.UUID, amount: int, at: datetime, kind: str = "payout"
) -> None:
    """A `payout` (money leaves the partner's payable) or its `payout_reversed`, stamped `at`."""
    payable = await service.owned_account(
        session, "partner_payable", "partner", partner, kind="liability"
    )
    clearing = await service.platform_account(session, "payout_clearing")
    sign = 1 if kind == "payout" else -1
    entry = JournalEntry(kind=kind, idempotency_key=f"test:{uuid.uuid4()}", created_at=at)
    session.add(entry)
    await session.flush()
    session.add_all(
        [
            Posting(entry_id=entry.id, account_id=payable.id, amount=sign * amount),
            Posting(entry_id=entry.id, account_id=clearing.id, amount=-sign * amount),
        ]
    )
    await session.flush()


def ist(year: int, month: int, day: int, hour: int = 12, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC) - timedelta(hours=5, minutes=30)


async def months(session: AsyncSession, partners: list[uuid.UUID], first: str, last: str):
    return await billing_api.partner_months(session, partners, first, last, until=UNTIL)


async def test_every_month_is_listed_with_zeros_for_a_partner_with_no_ledger(
    db_session: AsyncSession,
) -> None:
    found = await months(db_session, [uuid.uuid4()], "2026-08", "2026-10")

    assert [m.month for m in found.months] == ["2026-08", "2026-09", "2026-10"]
    assert all(m.earned_paise == m.paid_out_paise == 0 and not m.by_basis for m in found.months)
    assert found.owed_paise == 0


async def test_an_earning_counts_in_the_ist_month_of_the_work_not_of_its_posting(
    db_session: AsyncSession,
) -> None:
    kitchen = uuid.uuid4()
    # 23:59 IST on 31 May and 00:01 IST on 1 June: UTC says the same day, IST says two months.
    await earn(db_session, kitchen, "tiffin_portion", 2, 12_000, ist(2026, 5, 31, 23, 59))
    await earn(db_session, kitchen, "tiffin_portion", 1, 6_000, ist(2026, 6, 1, 0, 1))
    # Worked in May, posted in July: May's income, July's ledger.
    await earn(db_session, kitchen, "tiffin_portion", 3, 18_000, ist(2026, 5, 10), ist(2026, 7, 2))

    found = await months(db_session, [kitchen], "2026-05", "2026-07")

    by_month = {m.month: m for m in found.months}
    assert by_month["2026-05"].earned_paise == 30_000
    assert [(b.basis, b.units, b.amount_paise) for b in by_month["2026-05"].by_basis] == [
        ("tiffin_portion", 5, 30_000)
    ]
    assert by_month["2026-06"].earned_paise == 6_000
    assert by_month["2026-07"].earned_paise == 0
    assert found.owed_paise == 36_000


async def test_what_accrue_earning_posts_is_read_back_by_its_month(
    db_session: AsyncSession,
) -> None:
    partner = uuid.uuid4()
    await billing_api.accrue_earning(
        db_session,
        partner_id=partner,
        basis="dispatched_order",
        ref_type="order",
        ref_id=uuid.uuid4(),
        units=1,
        amount_paise=800,
        earned_at=ist(2026, 8, 15),
    )

    found = await billing_api.partner_months(
        db_session, [partner], "2026-08", "2026-09", until=datetime.now(UTC)
    )

    assert [m.earned_paise for m in found.months] == [800, 0]
    assert found.owed_paise == 0  # posted today: not yet in the books at the end of September


async def test_two_records_are_counted_per_basis_and_other_partners_never(
    db_session: AsyncSession,
) -> None:
    kitchen, captain, stranger = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await earn(db_session, kitchen, "tiffin_portion", 2, 12_000, ist(2026, 10, 3))
    await earn(db_session, captain, "bag_delivered", 1, 600, ist(2026, 10, 4))
    await earn(db_session, captain, "bag_delivered", 1, 600, ist(2026, 10, 5))
    await earn(db_session, stranger, "packed_order", 1, 400, ist(2026, 10, 5))

    found = await months(db_session, [kitchen, captain], "2026-10", "2026-10")

    (october,) = found.months
    assert october.earned_paise == 13_200
    assert {(b.partner_id, b.basis, b.units, b.amount_paise) for b in october.by_basis} == {
        (kitchen, "tiffin_portion", 2, 12_000),
        (captain, "bag_delivered", 2, 1_200),
    }
    assert found.owed_paise == 13_200


async def test_paid_out_counts_by_posting_month_and_a_reversal_cancels_its_payout(
    db_session: AsyncSession,
) -> None:
    partner = uuid.uuid4()
    await earn(db_session, partner, "tiffin_portion", 10, 60_000, ist(2026, 10, 27))
    # The payout for the week 27 Oct to 2 Nov is posted on Monday 3 Nov; this one, for an earlier
    # week, on Monday 5 Oct. Another goes to the bank and comes back.
    await earn(db_session, partner, "tiffin_portion", 5, 30_000, ist(2026, 9, 29))
    await settle(db_session, partner, 30_000, ist(2026, 10, 5, 10))
    await earn(db_session, partner, "tiffin_portion", 2, 12_000, ist(2026, 9, 2))
    await settle(db_session, partner, 12_000, ist(2026, 9, 7, 10))
    await settle(db_session, partner, 12_000, ist(2026, 9, 7, 10), kind="payout_reversed")

    found = await months(db_session, [partner], "2026-09", "2026-10")

    september, october = found.months
    assert (september.earned_paise, september.paid_out_paise) == (42_000, 0)
    assert (october.earned_paise, october.paid_out_paise) == (60_000, 30_000)
    # Owed: 102,000 earned less the 30,000 that was paid out; the reversed one is back.
    assert found.owed_paise == 72_000


async def test_owed_is_read_at_the_end_of_the_last_month_or_now(db_session: AsyncSession) -> None:
    partner = uuid.uuid4()
    await earn(db_session, partner, "packed_order", 1, 400, ist(2026, 9, 20))
    await earn(db_session, partner, "packed_order", 1, 400, ist(2026, 10, 10))
    await earn(db_session, partner, "packed_order", 1, 400, UNTIL + timedelta(hours=1))

    september = await months(db_session, [partner], "2026-09", "2026-09")
    october = await months(db_session, [partner], "2026-10", "2026-10")

    assert september.owed_paise == 400  # October's money is not owed yet at the end of September
    assert october.owed_paise == 800  # up to `until`; the entry an hour later is not there yet


async def test_a_range_the_calendar_does_not_allow_is_refused(db_session: AsyncSession) -> None:
    with pytest.raises(ValueError):
        await months(db_session, [uuid.uuid4()], "2026-10", "2026-09")
    with pytest.raises(ProblemError) as future:
        await months(db_session, [uuid.uuid4()], "2026-10", "2026-11")
    assert future.value.code == "billing.period_invalid"


# One earning or settlement of a random partner at a random time in the last 400 days.
step = st.tuples(
    st.sampled_from(["earn", "payout", "reversed"]),
    st.integers(0, 2),
    st.integers(1, 90_000),
    st.integers(2, 400 * 24),  # hours before UNTIL
)


async def reconcile(database_url: str, steps: list[tuple[str, int, int, int]]) -> None:
    engine = create_engine(database_url, pooled=False)
    partners = [uuid.uuid4() for _ in range(3)]
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            session = AsyncSession(bind=connection, expire_on_commit=False)
            try:
                for what, who, amount, hours in steps:
                    at = UNTIL - timedelta(hours=hours)
                    if what == "earn":
                        await earn(session, partners[who], "packed_order", 1, amount, at)
                    else:
                        kind = "payout" if what == "payout" else "payout_reversed"
                        await settle(session, partners[who], amount, at, kind)
                for first, last in (("2025-12", "2026-10"), ("2026-03", "2026-08")):
                    found = await billing_api.partner_months(
                        session, partners, first, last, until=UNTIL
                    )
                    closing = (
                        await statements.earnings_statement(
                            session, partners, statements.parse_period(last, UNTIL)
                        )
                    ).closing_paise
                    assert found.owed_paise == closing
                    assert sum(m.earned_paise for m in found.months) == sum(
                        a for w, _, a, h in steps if w == "earn" and _in(first, last, h)
                    )
            finally:
                await session.close()
                await transaction.rollback()
    finally:
        await engine.dispose()


def _in(first: str, last: str, hours: int) -> bool:
    """Whether work done `hours` before UNTIL falls in the months first..last (IST)."""
    month = (UNTIL - timedelta(hours=hours) + timedelta(hours=5, minutes=30)).strftime("%Y-%m")
    return first <= month <= last


@settings(max_examples=40, deadline=None)
@given(steps=st.lists(step, max_size=12))
def test_owed_equals_the_earnings_statements_closing_balance_for_any_entries(
    database_url: str, steps: list[tuple[str, int, int, int]]
) -> None:
    asyncio.run(reconcile(database_url, steps))
