"""Races on the assistant's rows, on separate connections (so these commit; they clean up after
themselves): two first turns of the day at once, and two decisions on one card at once."""

import asyncio
import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from gharbasket.modules.assistant import service
from gharbasket.modules.assistant.models import Proposal, Session, UsageDaily
from gharbasket.platform.db import create_engine
from gharbasket.platform.errors import ProblemError

pytestmark = [pytest.mark.anyio, pytest.mark.services]

DAY = date(2026, 10, 10)


async def test_two_first_turns_of_the_day_at_once_are_both_counted(database_url: str) -> None:
    """Both reserve the day's row (one inserts it, the other finds it), both are with the model
    at the same time (no lock is held across the call), and both turns' tokens are added."""
    engine = create_engine(database_url, pooled=False)
    user = uuid.uuid4()
    reserved = asyncio.Barrier(2)

    async def a_turn(tokens_in: int) -> int:
        async with AsyncSession(engine) as session:
            await service.reserve_turn(session, user, DAY, cap=60_000)
            await session.commit()
            await asyncio.wait_for(reserved.wait(), timeout=10)  # "asking the model"
            total = await service.record_tokens(session, user, DAY, tokens_in, 80)
            await session.commit()
            return total

    try:
        totals = await asyncio.gather(a_turn(1000), a_turn(500))
        async with AsyncSession(engine) as session:
            usage = (
                await session.execute(select(UsageDaily).where(UsageDaily.user_id == user))
            ).scalar_one()
        assert (usage.turns, usage.tokens_in, usage.tokens_out) == (2, 1500, 160)
        assert max(totals) == 1660 and min(totals) in (580, 1080)

        # Over the cap, the next turn is refused before anything is asked.
        async with AsyncSession(engine) as session:
            with pytest.raises(ProblemError) as refused:
                await service.reserve_turn(session, user, DAY, cap=1600)
        assert refused.value.code == "assistant.daily_cap"
    finally:
        async with AsyncSession(engine) as session:
            await session.execute(delete(UsageDaily).where(UsageDaily.user_id == user))
            await session.commit()
        await engine.dispose()


async def test_two_decisions_on_one_card_at_once_apply_once(database_url: str) -> None:
    """The card's row is locked while it is decided: the second waits, then finds it decided
    (confirm takes the same lock through `get_proposal(lock=True)`)."""
    engine = create_engine(database_url, pooled=False)
    user = uuid.uuid4()

    async def a_card() -> uuid.UUID:
        async with AsyncSession(engine) as session:
            chat = Session(user_id=user, household_id=uuid.uuid4(), locale="hi")
            session.add(chat)
            await session.flush()
            card = Proposal(
                session_id=chat.id,
                user_id=user,
                household_id=chat.household_id,
                action="skip_week",
                payload={},
                summary="Skip",
                expires_at=service.now() + timedelta(minutes=15),
            )
            session.add(card)
            await session.flush()
            card_id = card.id
            await session.commit()
            return card_id

    async def decide(card_id: uuid.UUID) -> str:
        async with AsyncSession(engine) as session:
            try:
                proposal = await service.get_proposal(session, user, card_id, lock=True)
                await asyncio.sleep(0.05)  # hold the lock while the other one waits
                await service.reject(session, proposal)
                await session.commit()
                return "decided"
            except ProblemError as exc:
                return exc.code

    try:
        card_id = await a_card()
        results = await asyncio.gather(decide(card_id), decide(card_id))
        assert sorted(results) == ["assistant.proposal_decided", "decided"]
    finally:
        async with AsyncSession(engine) as session:
            await session.execute(delete(Session).where(Session.user_id == user))
            await session.commit()
        await engine.dispose()
