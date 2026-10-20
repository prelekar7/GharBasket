"""Property test of Kindness points (M5 §3.6, §9): for any sequence of credits (some replayed),
donations and expiry runs, the lots' Σ remaining equals the ledger balance, a donation never
uses an expired lot, every entry sums to zero per currency, and INR is never touched."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import event, given, settings
from hypothesis import strategies as st
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.billing import kindness, service
from gharbasket.modules.billing.models import LedgerAccount, PointsLot, Posting
from gharbasket.platform.db import create_engine
from gharbasket.platform.errors import ProblemError

pytestmark = pytest.mark.services

GOAL = 50
SOURCES = [uuid.UUID(int=n + 1) for n in range(5)]
step = st.one_of(
    # credit: points, which source, its expiry in days from now (negative: already past)
    st.tuples(st.just("credit"), st.integers(1, 250), st.integers(0, 4), st.integers(-3, 30)),
    st.tuples(st.just("donate"), st.integers(1, 3), st.integers(0, 2), st.just(0)),
    st.tuples(st.just("expire"), st.just(0), st.just(0), st.just(0)),
)


async def run(database_url: str, steps: list[tuple[str, int, int, int]]) -> None:
    engine = create_engine(database_url, pooled=False)
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            session = AsyncSession(bind=connection, expire_on_commit=False)
            try:
                await check(session, uuid.uuid4(), steps)
            finally:
                await session.close()
                await transaction.rollback()
    finally:
        await engine.dispose()


async def live(session: AsyncSession, user: uuid.UUID, now: datetime) -> int:
    total = await session.execute(
        select(func.coalesce(func.sum(PointsLot.remaining), 0)).where(
            PointsLot.user_id == user, PointsLot.expires_at > now
        )
    )
    return int(total.scalar_one())


async def all_remaining(session: AsyncSession, user: uuid.UUID) -> int:
    total = await session.execute(
        select(func.coalesce(func.sum(PointsLot.remaining), 0)).where(PointsLot.user_id == user)
    )
    return int(total.scalar_one())


async def check(session: AsyncSession, user: uuid.UUID, steps: list[tuple[str, int, int, int]]):
    now = datetime.now(UTC)
    credited: dict[uuid.UUID, int] = {}
    for kind, amount, n, days in steps:
        if kind == "credit":
            source = SOURCES[n]
            try:
                entry = await kindness.credit_points(session, user, amount, "donation", source)
            except ProblemError:
                assert credited[source] != amount
                event("credit conflict")
                continue
            if source in credited:
                event("credit replay")
            else:
                credited[source] = amount
                await session.execute(
                    update(PointsLot)
                    .where(PointsLot.entry_id == entry.id)
                    .values(expires_at=now + timedelta(days=days, minutes=1))
                )
        elif kind == "donate":
            at = datetime.now(UTC)
            usable = await live(session, user, at)
            past = await all_remaining(session, user) - usable
            try:
                await kindness.donate(session, user, amount, GOAL, f"key-{n}")
            except ProblemError as problem:
                assert problem.code in ("billing.not_enough_points", "billing.entry_conflict")
                if problem.code == "billing.not_enough_points":
                    assert usable < amount * GOAL
                event(problem.code)
                continue
            event("donated")
            session.expire_all()
            assert await all_remaining(session, user) - await live(session, user, at) == past
        else:
            await kindness.expire_lots(session, user, datetime.now(UTC))

        session.expire_all()
        account = await service.find_account(session, "kindness", "user", user, "KINDNESS_PTS")
        ledger = 0 if account is None else await service.balance(session, account)
        assert await all_remaining(session, user) == ledger >= 0

    await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))  # every entry balanced
    inr = await session.execute(
        select(func.count())
        .select_from(Posting)
        .join(LedgerAccount, LedgerAccount.id == Posting.account_id)
        .where(LedgerAccount.currency == "INR", LedgerAccount.owner_id == user)
    )
    assert inr.scalar_one() == 0


@settings(max_examples=60, deadline=None)
@given(steps=st.lists(step, max_size=12))
def test_points_lots_always_match_the_ledger(
    database_url: str, steps: list[tuple[str, int, int, int]]
) -> None:
    asyncio.run(run(database_url, steps))
