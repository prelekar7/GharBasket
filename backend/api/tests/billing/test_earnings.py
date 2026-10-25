"""Partner earnings in the ledger (M5 §3.5): accruing once per (partner, basis, order), the week of
a partner's records (IST Monday to Sunday, by when the work was done) and the rolling total."""

import asyncio
import os
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from alembic import command
from sqlalchemy import make_url, text
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import api as billing_api
from gharbasket.modules.billing import service
from gharbasket.platform.db import create_engine
from gharbasket.platform.errors import ProblemError

from ..conftest import LOCAL_TEST_DATABASE_URL, alembic_config, run_on_server

pytestmark = [pytest.mark.anyio, pytest.mark.services]

# Monday 11 January 2027, 10:00 IST, in UTC.
MONDAY = datetime(2027, 1, 11, 4, 30, tzinfo=UTC)


async def accrue(
    session: AsyncSession,
    partner: uuid.UUID,
    basis: str,
    order: uuid.UUID,
    units: int,
    amount: int,
    at: datetime,
    occurrence: uuid.UUID | None = None,
) -> uuid.UUID:
    return await billing_api.accrue_earning(
        session,
        partner_id=partner,
        basis=basis,
        ref_type="order",
        ref_id=order,
        units=units,
        amount_paise=amount,
        earned_at=at,
        occurrence=occurrence,
    )


async def test_an_earning_is_one_balanced_entry_and_a_replay_pays_nothing_twice(
    db_session: AsyncSession,
) -> None:
    partner, order = uuid.uuid4(), uuid.uuid4()

    first = await accrue(db_session, partner, "tiffin_portion", order, 3, 18_000, MONDAY)
    again = await accrue(db_session, partner, "tiffin_portion", order, 3, 18_000, MONDAY)

    assert first == again
    payable = await service.find_account(db_session, "partner_payable", "partner", partner)
    expense = await service.platform_account(db_session, "fulfilment_expense")
    assert payable is not None and await service.balance(db_session, payable) == 18_000
    assert expense.kind == "expense"
    # The same cause with another amount is the ledger's conflict, never a second entry; another
    # basis or order is a new entry.
    with pytest.raises(ProblemError) as conflict:
        await accrue(db_session, partner, "tiffin_portion", order, 3, 12_000, MONDAY)
    assert conflict.value.code == "billing.entry_conflict"
    await accrue(db_session, partner, "bag_delivered", order, 1, 600, MONDAY)
    assert await service.balance(db_session, payable) == 18_600


async def test_only_positive_work_on_a_known_basis_for_an_order_is_recorded(
    db_session: AsyncSession,
) -> None:
    partner, order = uuid.uuid4(), uuid.uuid4()
    for basis, units, amount, ref_type in (
        ("tiffin_portion", 0, 600, "order"),
        ("tiffin_portion", 1, 0, "order"),
        ("tiffin_portion", 1, -5, "order"),
        ("coffee", 1, 600, "order"),
        ("tiffin_portion", 1, 600, "payout"),
    ):
        with pytest.raises(ValueError):
            await billing_api.accrue_earning(
                db_session,
                partner_id=partner,
                basis=basis,
                ref_type=ref_type,
                ref_id=order,
                units=units,
                amount_paise=amount,
                earned_at=MONDAY,
            )


async def test_the_week_sums_the_ledger_by_when_the_work_was_done_in_ist(
    db_session: AsyncSession,
) -> None:
    kitchen, rider, other = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    orders = [uuid.uuid4() for _ in range(6)]
    await accrue(db_session, kitchen, "tiffin_portion", orders[0], 2, 12_000, MONDAY)
    await accrue(
        db_session, kitchen, "tiffin_portion", orders[1], 1, 6_000, MONDAY + timedelta(days=2)
    )
    await accrue(
        db_session, rider, "dispatched_order", orders[2], 1, 800, MONDAY + timedelta(days=6)
    )
    # 23:30 IST on the Sunday still belongs to the week; 00:15 IST on the next Monday does not.
    sunday_late = datetime(2027, 1, 17, 18, 0, tzinfo=UTC)
    await accrue(db_session, kitchen, "tiffin_portion", orders[3], 1, 6_000, sunday_late)
    await accrue(
        db_session,
        kitchen,
        "tiffin_portion",
        orders[4],
        1,
        6_000,
        sunday_late + timedelta(minutes=45),
    )
    await accrue(
        db_session, kitchen, "tiffin_portion", orders[5], 1, 6_000, MONDAY - timedelta(days=1)
    )
    await accrue(db_session, other, "tiffin_portion", uuid.uuid4(), 9, 54_000, MONDAY)

    week = await billing_api.partner_week(db_session, [kitchen, rider], "2027-W02")

    assert (week.start, week.end) == (date(2027, 1, 11), date(2027, 1, 17))
    assert week.total_paise == 12_000 + 6_000 + 800 + 6_000
    assert [(b.partner_id, b.basis, b.units, b.amount_paise) for b in week.by_basis] == sorted(
        [
            (kitchen, "tiffin_portion", 4, 24_000),
            (rider, "dispatched_order", 1, 800),
        ],
        key=str,
    )
    assert [(d.day.isoformat(), d.amount_paise) for d in week.days] == [
        ("2027-01-11", 12_000),
        ("2027-01-12", 0),
        ("2027-01-13", 6_000),
        ("2027-01-14", 0),
        ("2027-01-15", 0),
        ("2027-01-16", 0),
        ("2027-01-17", 6_800),
    ]
    assert [e.amount_paise for e in week.entries] == [6_000, 800, 6_000, 12_000]  # newest first
    assert sum(e.amount_paise for e in week.entries) == week.total_paise
    assert {e.ref_type for e in week.entries} == {"order"} and week.payouts == []
    empty = await billing_api.partner_week(db_session, [], "2027-W02")
    assert (empty.total_paise, len(empty.days), empty.entries) == (0, 7, [])


async def test_rolling_earnings_count_one_basis_from_a_date_on(db_session: AsyncSession) -> None:
    kitchen, idle = uuid.uuid4(), uuid.uuid4()
    await accrue(
        db_session, kitchen, "tiffin_portion", uuid.uuid4(), 1, 6_000, MONDAY - timedelta(days=30)
    )
    await accrue(
        db_session, kitchen, "tiffin_portion", uuid.uuid4(), 2, 12_000, MONDAY - timedelta(days=3)
    )
    await accrue(db_session, kitchen, "bag_delivered", uuid.uuid4(), 1, 600, MONDAY)

    since = MONDAY - timedelta(days=28)
    got = await billing_api.partner_rolling_earnings(
        db_session, [kitchen, idle], "tiffin_portion", since
    )

    assert got == {kitchen: 12_000, idle: 0}


async def test_work_that_repeats_on_one_order_is_paid_per_occurrence_and_replays_stay_once(
    db_session: AsyncSession,
) -> None:
    partner, order = uuid.uuid4(), uuid.uuid4()
    first, second = uuid.uuid4(), uuid.uuid4()

    one = await accrue(db_session, partner, "dispatched_order", order, 1, 800, MONDAY, first)
    replay = await accrue(db_session, partner, "dispatched_order", order, 1, 800, MONDAY, first)
    two = await accrue(db_session, partner, "dispatched_order", order, 1, 900, MONDAY, second)

    assert one == replay and one != two
    payable = await service.find_account(db_session, "partner_payable", "partner", partner)
    assert payable is not None and await service.balance(db_session, payable) == 1_700
    # The same trip again with another amount is the ledger's conflict, not a second payment.
    with pytest.raises(ProblemError) as conflict:
        await accrue(db_session, partner, "dispatched_order", order, 1, 950, MONDAY, second)
    assert conflict.value.code == "billing.entry_conflict"


def test_the_migration_downgrades_even_when_earnings_exist() -> None:
    """The ledger refuses DELETE, so the downgrade has to clear the earnings itself."""
    base = os.environ.get("TEST_DATABASE_URL", LOCAL_TEST_DATABASE_URL)
    name = f"{make_url(base).database}_downgrade"
    url = make_url(base).set(database=name).render_as_string(hide_password=False)

    def fetch(sql: str) -> Any:
        async def go() -> Any:
            engine = create_engine(url, pooled=False)
            try:
                async with engine.connect() as connection:
                    return (await connection.execute(text(sql))).all()
            finally:
                await engine.dispose()

        return asyncio.run(go())

    async def accrue_one() -> None:
        engine = create_engine(url, pooled=False)
        try:
            async with AsyncSession(engine) as session:
                await accrue(
                    session, uuid.uuid4(), "tiffin_portion", uuid.uuid4(), 2, 12_000, MONDAY
                )
                await session.commit()
        finally:
            await engine.dispose()

    run_on_server(base, f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
    run_on_server(base, f"CREATE DATABASE {name}")
    try:
        config = alembic_config(url)
        command.upgrade(config, "head")
        asyncio.run(accrue_one())
        command.downgrade(config, "8d3f2a61b7c4")
        left = fetch(
            "SELECT (SELECT count(*) FROM billing.journal_entries WHERE kind = 'earning'),"
            " (SELECT count(*) FROM billing.ledger_accounts"
            "  WHERE code IN ('fulfilment_expense', 'partner_payable')),"
            " (SELECT count(*) FROM pg_trigger WHERE tgname LIKE '%append_only'"
            "  AND NOT tgisinternal"
            "  AND tgenabled = 'D')"
        )
        assert left == [(0, 0, 0)]  # nothing of the earnings left, the guard triggers back on
        command.upgrade(config, "head")  # and it goes up again
    finally:
        run_on_server(base, f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
